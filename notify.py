"""Gleame install/uninstall alerts.

GitHub Actions runs this every 10 minutes. It asks the Shopify Partner API for
installs and uninstalls from the last 24 hours, and sends each one it hasn't
already sent to ntfy, Slack and email. `python3 notify.py --test` sends a test
message to all three instead.

This repository is public, so this script never prints store names to the log,
and state.json only holds scrambled fingerprints of events already sent.
"""
import hashlib
import json
import os
import smtplib
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

ORG_ID = "4405764"
APP_GID = "gid://partners/App/277725020161"
API_VERSION = "2026-10"
API_URL = f"https://partners.shopify.com/{ORG_ID}/api/{API_VERSION}/graphql.json"

PACIFIC = ZoneInfo("America/Los_Angeles")
HERE = Path(__file__).parent
STATE_FILE = HERE / "state.json"
IGNORE_FILE = HERE / "ignore-list.txt"
LOOKBACK = timedelta(hours=24)  # each run re-reads a full day, so a few skipped runs lose nothing
REMEMBER = timedelta(days=3)  # how long to remember an event was sent (must be longer than LOOKBACK)
HEARTBEAT_HOUR = 9  # daily "still running" message goes out on the first run after 9am Pacific

EVENTS_QUERY = """
query($app: ID!, $since: DateTime, $after: String) {
  app(id: $app) {
    events(first: 100, after: $after, occurredAtMin: $since,
           types: [RELATIONSHIP_INSTALLED, RELATIONSHIP_UNINSTALLED]) {
      edges {
        cursor
        node {
          type
          occurredAt
          shop { id name myshopifyDomain }
          ... on RelationshipUninstalled { reason description }
        }
      }
      pageInfo { hasNextPage }
    }
  }
}
"""

LAST_INSTALL_QUERY = """
query($app: ID!, $shop: ID!, $before: DateTime) {
  app(id: $app) {
    events(first: 1, shopId: $shop, occurredAtMax: $before, types: [RELATIONSHIP_INSTALLED]) {
      edges { node { occurredAt } }
    }
  }
}
"""


def secret(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"the GitHub secret {name} is missing")
    return value


def post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


# ---------- Shopify ----------

def graphql(query, variables):
    body = post(
        API_URL,
        json.dumps({"query": query, "variables": variables}).encode(),
        {"Content-Type": "application/json", "X-Shopify-Access-Token": secret("SHOPIFY_PARTNER_TOKEN")},
    )
    result = json.loads(body)
    if result.get("errors"):
        raise RuntimeError(f"Shopify returned errors: {result['errors']}")
    if result["data"]["app"] is None:
        raise RuntimeError("Shopify can't see the app; the token needs the 'Manage apps' permission")
    return result["data"]["app"]


def fetch_events(since):
    events, after = [], None
    while True:
        page = graphql(EVENTS_QUERY, {"app": APP_GID, "since": iso(since), "after": after})["events"]
        events += [edge["node"] for edge in page["edges"]]
        if not page["pageInfo"]["hasNextPage"]:
            return events
        after = page["edges"][-1]["cursor"]


def time_installed(event):
    """How long the store had the app before this uninstall, or None if unknown."""
    try:
        edges = graphql(LAST_INSTALL_QUERY, {
            "app": APP_GID, "shop": event["shop"]["id"], "before": event["occurredAt"],
        })["events"]["edges"]
    except Exception:
        return None
    if not edges:
        return None
    return parse(event["occurredAt"]) - parse(edges[0]["node"]["occurredAt"])


# ---------- Messages ----------

def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def pacific(dt):
    return dt.astimezone(PACIFIC).strftime("%b %-d, %-I:%M %p %Z")


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def friendly_duration(delta):
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "less than a minute"
    if minutes < 60:
        return plural(minutes, "minute")
    hours = minutes // 60
    if hours < 48:
        return plural(hours, "hour")
    return plural(hours // 24, "day")


def describe(event):
    """Returns (subject, message, ntfy priority, ntfy tag) for one event."""
    shop = f"{event['shop']['name']} ({event['shop']['myshopifyDomain']})"
    if event["type"] == "RELATIONSHIP_INSTALLED":
        message = f"🟢 New Gleame install: {shop} at {pacific(parse(event['occurredAt']))}"
        return f"🟢 New Gleame install: {event['shop']['name']}", message, "high", "green_circle"

    duration = time_installed(event)
    after = f" after {friendly_duration(duration)}" if duration is not None else ""
    reason = event.get("reason") or "none given"
    if event.get("description"):
        reason += f' ("{event["description"]}")'
    message = f"🔴 Gleame uninstall: {shop}{after}. Reason: {reason}"
    return f"🔴 Gleame uninstall: {event['shop']['name']}", message, "default", "red_circle"


# ---------- Channels ----------

def send_ntfy(subject, message, priority, tag):
    post(f"https://ntfy.sh/{secret('NTFY_TOPIC')}", message.encode(), {
        "Title": "Gleame alerts", "Priority": priority, "Tags": tag,
    })


def send_slack(subject, message, priority, tag):
    post(secret("SLACK_WEBHOOK_URL"), json.dumps({"text": message}).encode(),
         {"Content-Type": "application/json"})


def send_email(subject, message, priority, tag):
    sender = secret("GMAIL_ADDRESS")
    email = EmailMessage()
    email["From"] = sender
    email["To"] = os.environ.get("EMAIL_TO", "").strip() or sender
    email["Subject"] = subject
    email.set_content(message)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(sender, secret("GMAIL_APP_PASSWORD").replace(" ", ""))
        server.send_message(email)


# Each channel, and the secret that switches it on. A channel with no secret is skipped,
# so you can add one later just by adding its secret in GitHub.
CHANNELS = {
    "ntfy": (send_ntfy, "NTFY_TOPIC"),
    "Slack": (send_slack, "SLACK_WEBHOOK_URL"),
    "email": (send_email, "GMAIL_APP_PASSWORD"),
}


def active_channels():
    return {name: send for name, (send, key) in CHANNELS.items() if os.environ.get(key, "").strip()}


def notify_all(subject, message, priority, tag):
    """Sends to every set-up channel, even if some fail. Returns the names of the ones that failed."""
    channels = active_channels()
    for name in CHANNELS.keys() - channels.keys():
        print(f"  {name}: skipped (not set up)")
    if not channels:
        print("  No channels are set up - add the GitHub secrets.")
        return ["all channels"]
    failed = []
    for name, send in channels.items():
        try:
            send(subject, message, priority, tag)
            print(f"  {name}: sent")
        except Exception as e:
            print(f"  {name}: FAILED - {e}")
            failed.append(name)
    return failed


# ---------- Memory ----------

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def fingerprint(event):
    raw = f"{event['type']}|{event['shop']['id']}|{event['occurredAt']}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_ignore_list():
    if not IGNORE_FILE.exists():
        return set()
    lines = (line.strip().lower() for line in IGNORE_FILE.read_text().splitlines())
    return {line for line in lines if line and not line.startswith("#")}


# ---------- Main ----------

def run_test(now):
    try:
        events = fetch_events(now - timedelta(days=7))
        uninstalls = [event for event in events if event["type"] == "RELATIONSHIP_UNINSTALLED"]
        if uninstalls:  # also check the "how long installed" lookup works
            graphql(LAST_INSTALL_QUERY, {"app": APP_GID, "shop": uninstalls[0]["shop"]["id"],
                                         "before": uninstalls[0]["occurredAt"]})
        shopify = f"Shopify connection OK ({plural(len(events), 'install/uninstall')} in the last 7 days)"
    except Exception as e:
        shopify = f"Shopify connection FAILED: {e}"
    print(shopify)
    message = f"🧪 Test from Gleame install alerts at {pacific(now)}. {shopify}. If you can read this, this channel works."
    failed = notify_all("🧪 Gleame alerts test", message, "high", "test_tube")
    if failed or "FAILED" in shopify:
        sys.exit("Test finished with problems - see the lines above.")
    print(f"Test passed: sent to {', '.join(active_channels())}.")


def main():
    now = datetime.now(timezone.utc)
    if "--test" in sys.argv:
        return run_test(now)

    state = load_state()
    seen = state.get("seen", {})
    try:
        events = fetch_events(now - LOOKBACK)
    except Exception as e:
        sys.exit(f"Could not get events from Shopify: {e}")
    events.sort(key=lambda event: event["occurredAt"])

    if not state.get("started"):
        # Very first run: remember what's already there so you aren't flooded with old events.
        for event in events:
            seen[fingerprint(event)] = event["occurredAt"]
        state.update(started=iso(now), seen=seen)
        save_state(state)
        print(f"First run: marked {len(events)} past events as already seen. Alerts start now.")
        return

    ignored = load_ignore_list()
    counts = state.setdefault("counts", {"installs": 0, "uninstalls": 0})
    problems = []
    for event in events:
        key = fingerprint(event)
        if key in seen:
            continue
        kind = "install" if event["type"] == "RELATIONSHIP_INSTALLED" else "uninstall"
        if event["shop"]["myshopifyDomain"].lower() in ignored:
            print(f"Skipped an {kind} from a store on the ignore list.")
            seen[key] = event["occurredAt"]
            continue
        print(f"New {kind}:")
        failed = notify_all(*describe(event))
        problems += failed
        if len(failed) >= len(active_channels()):
            continue  # nothing got through, so try this one again next run
        seen[key] = event["occurredAt"]
        counts[kind + "s"] += 1

    # Forget events old enough that Shopify won't return them again.
    cutoff = now - REMEMBER
    state["seen"] = {key: when for key, when in seen.items() if parse(when) > cutoff}

    local = now.astimezone(PACIFIC)
    if local.hour >= HEARTBEAT_HOUR and state.get("last_heartbeat") != local.date().isoformat():
        message = (f"✅ Gleame alerts still running. Since the last check-in: "
                   f"{plural(counts['installs'], 'install')}, {plural(counts['uninstalls'], 'uninstall')}.")
        try:
            send_ntfy("", message, "low", "white_check_mark")
            print("Daily check-in sent to ntfy.")
            state["last_heartbeat"] = local.date().isoformat()
            state["counts"] = {"installs": 0, "uninstalls": 0}
        except Exception as e:
            print(f"Daily check-in FAILED - {e}")
            problems.append("daily check-in")

    save_state(state)
    if problems:
        sys.exit(f"Some messages didn't send: {', '.join(problems)}")
    print("Done.")


if __name__ == "__main__":
    main()
