"""Step 1: check that the Partner API token works.

Asks for the token at a hidden prompt (nothing shows while you paste),
then fetches the last 30 days of installs/uninstalls for Gleame.
"""
import getpass
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ORG_ID = "4405764"
APP_GID = "gid://partners/App/277725020161"
API_VERSION = "2026-10"
URL = f"https://partners.shopify.com/{ORG_ID}/api/{API_VERSION}/graphql.json"

QUERY = """
query($app: ID!, $since: DateTime) {
  app(id: $app) {
    name
    events(first: 20, types: [RELATIONSHIP_INSTALLED, RELATIONSHIP_UNINSTALLED], occurredAtMin: $since) {
      edges { node {
        type
        occurredAt
        shop { name myshopifyDomain }
        ... on RelationshipUninstalled { reason description }
      } }
    }
  }
}
"""

raw = getpass.getpass("Paste your Partner API token, then press Return (it stays hidden): ")
# Pull out just the token, ignoring invisible paste characters or a double paste.
match = re.search(r"prtapi_[0-9a-f]+", raw)
token = match.group(0) if match else "".join(c for c in raw if c.isprintable()).strip()
if not token:
    sys.exit("No token entered.")

since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
req = urllib.request.Request(
    URL,
    data=json.dumps({"query": QUERY, "variables": {"app": APP_GID, "since": since}}).encode(),
    headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token},
)

print(f"\nToken starts with: {token[:7]}...  (length {len(token)})")
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
except urllib.error.HTTPError as e:
    text = e.read().decode(errors="replace")[:500]
    print(f"\nFAILED: HTTP {e.code}\n{text}\n")
    if e.code in (401, 403):
        print("Plain English: Shopify does not accept this token. It may be mistyped, revoked,")
        print("or not a *Partner API* token (Admin API tokens from a store won't work here).")
    elif e.code == 404:
        print("Plain English: the address was wrong (organization ID or API version).")
    sys.exit(1)
except Exception as e:
    print(f"\nFAILED: could not reach Shopify: {e}")
    sys.exit(1)

if body.get("errors"):
    print("\nFAILED: Shopify answered, but with errors:")
    print(json.dumps(body["errors"], indent=2))
    sys.exit(1)

app = body["data"]["app"]
if app is None:
    print("\nFAILED: the token works, but it can't see app 277725020161.")
    print("Plain English: the token's API client is missing the 'Manage apps' permission,")
    print("or the app belongs to a different Partner organization.")
    sys.exit(1)

edges = app["events"]["edges"]
print(f"\nSUCCESS! Connected to app: {app['name']}")
print(f"Install/uninstall events in the last 30 days: {len(edges)} (showing up to 20)\n")
for edge in edges:
    n = edge["node"]
    line = f"  {n['occurredAt']}  {n['type']:<26} {n['shop']['name']} ({n['shop']['myshopifyDomain']})"
    if n["type"] == "RELATIONSHIP_UNINSTALLED":
        line += f"  reason: {n.get('reason') or '-'}"
    print(line)
