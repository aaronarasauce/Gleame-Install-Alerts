# Gleame install alerts

Every 10 minutes, GitHub checks Shopify for new installs and uninstalls of Gleame
and sends each one to your phone (ntfy), Slack (#new-installs) and email.
Once a day at about 9am Pacific, your phone gets a quiet "still running" message in ntfy.

## Files

| File | What it is |
|---|---|
| `notify.py` | The script that does everything |
| `ignore-list.txt` | Stores to never alert about (your dev stores, Shopify reviewer stores) |
| `.github/workflows/install-alerts.yml` | Tells GitHub to run the script every 10 minutes |
| `state.json` | Created automatically. Remembers which alerts were already sent (scrambled, no store names) |
| `test_token.py` | Run on your Mac to check a Partner API token works |

Passwords and tokens are **not** in any file. They're stored as GitHub secrets
(Settings → Secrets and variables → Actions):
`SHOPIFY_PARTNER_TOKEN`, `NTFY_TOPIC`, `SLACK_WEBHOOK_URL`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`,
and optionally `EMAIL_TO` (if alerts should go to a different address than the Gmail account).

## Add a store to the ignore list

1. Click `ignore-list.txt` above, then the ✏️ pencil icon (top right of the file).
2. Add the store's myshopify domain on a new line at the bottom, e.g. `gleame-dev.myshopify.com`
3. Click **Commit changes…**, then **Commit changes** again.

It takes effect on the next run.

## Send a test message

**Actions** tab → **Install alerts** (left side) → **Run workflow** → leave "Send a test message" ticked → **Run workflow**.

## If it breaks

- **No "still running" message in ntfy by about 10am Pacific?** Something is wrong.
- Open the **Actions** tab. A ✅ means the run worked and a ❌ means it failed.
  Click a ❌ run, then **check**, then the step with the ❌, and read the last few lines.
  The message says in plain words what failed (e.g. "Slack: FAILED", "the GitHub secret … is missing",
  "Could not get events from Shopify").
- GitHub also emails you when a run fails.
- If you see a yellow banner saying the workflow is disabled, click **Enable workflow**.
- Common fixes: a token or password was revoked, so make a new one and update the matching secret
  (Settings → Secrets and variables → Actions → click the secret's name → enter the new value → **Update secret**).
