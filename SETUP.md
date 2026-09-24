# Setup guide

This guide takes a fresh computer to a running dashboard. Everything except the accounts and keys is already built. Run `uv run cultph doctor` at any point to see what's done and what to do next.

## 1. Install (about 5 minutes)

```bash
git clone https://github.com/akashub/cult-product-health.git
cd cult-product-health
```

Copy the two files from the handover zip you received privately, `config.private.yaml` and `PLAN.private.md`, into this folder. They hold the Cult product list and sheet layout, and are never stored on GitHub.

- macOS / Linux: `./scripts/bootstrap.sh`
- Windows (PowerShell): `powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1`

This installs `uv`, the Python packages and a headless Chromium, then runs `doctor`.

## 2. Keys and alert channels

```bash
uv run cultph setup
```

It asks for each value in turn. Press Enter to skip any you don't have yet. Values are saved to `data/.env`, which is readable only by you and never committed.

| Value | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys. Needed for the AI review labels. |
| Telegram (optional) | Message **@BotFather** → `/newbot` to get the bot token. Send your bot a message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`. |
| Slack (optional) | Slack → Apps → Incoming Webhooks → create a webhook URL. |
| Email (optional) | For Gmail: host `smtp.gmail.com`, port `465`, your address, and an **app password** (Google Account → Security → App passwords). |
| `DASHBOARD_PASSWORD` (optional) | Any password. Set one if the dashboard will be reachable from other devices. |

Desktop notifications (macOS or Linux) work without any setup. Test your channels with `uv run cultph alerts --test`.

## 3. Amazon sign-in (needed to read every review)

```bash
uv run cultph amazon-login
```

A browser window opens. Sign in, **preferably with a secondary Amazon account**, since the scraper visits pages automatically. The window closes by itself once you're in, and the session is kept under `data/amazon_profile`.

Without this, Amazon only shows about 8 "top reviews" per product. Ratings and the 4.1 calculator still work. Flipkart doesn't need a login.

## 4. Google Sheets (read the return and exchange sheets directly)

The tool can also read a local `.xlsx` export (`source.type: xlsx` in the config). To read the live Google Sheets instead, pick **one** of these options.

### Option A: sign in with Gmail (OAuth)
1. At console.cloud.google.com, create a project, then enable the **Google Sheets API** and the **Google Drive API**.
2. Go to **OAuth consent screen**, choose *External*, and add your Gmail address as a test user.
   - ⚠️ While the app is in **Testing** mode, Google makes the sign-in expire every **7 days**. To avoid signing in again every week, click **Publish app** (switch it to *In production*). You'll see an "unverified app" warning during sign-in, which is fine for personal use.
3. Go to **Credentials → Create credentials → OAuth client ID → Desktop app**. Download the JSON and save it as `data/google_client.json`.
4. Run `uv run cultph sheets`. A browser opens for the Gmail sign-in, then it lists the sheets you can access.
5. In `config.private.yaml`, set:
   ```yaml
   source:
     type: gsheet
     spreadsheet_id: "<id from the list>"
     auth: oauth
   ```

### Option B: service account (never expires; best for scheduled runs)
1. In the same Cloud project, go to **IAM → Service accounts → Create**. Then **Keys → Add key → JSON**, and save it as `data/google_service_account.json`.
2. Share each sheet with the service account's email (`…@….iam.gserviceaccount.com`) as a *Viewer*.
3. Set `auth: service_account` in `config.private.yaml`.

If the Google Sheet's tab or column names differ from the xlsx, adjust the `tabs:` section of the config. `cultph sync` tells you exactly which column it couldn't find.

## 5. Check everything for real

```bash
uv run cultph doctor --live
```

This makes one real call to each service: it opens the Amazon review list, labels one sample review with the AI, and opens the Google Sheet. Anything that fails comes with its next step.

## 6. Run it

```bash
uv run cultph run                         # sheet sync → Amazon → Flipkart → AI labels → alerts
uv run streamlit run app/dashboard.py     # the dashboard (opens in your browser)
```

The first `run` records an alerts baseline and sends nothing. After that, only new events raise alerts.

### Keep it running
- **Any OS:** `uv run cultph watch --every 60` runs everything every hour while the terminal stays open.
- **macOS, in the background:** `uv run cultph schedule --every 60` writes a launchd file and prints the one command that turns it on (and the one that turns it off).
- Either way, runs happen only while the computer is awake.

### Open the dashboard from your phone
1. Set `DASHBOARD_PASSWORD` (step 2).
2. Install **Tailscale** on the computer and the phone, and sign in to both with the same account.
3. Run `uv run streamlit run app/dashboard.py --server.address 0.0.0.0`.
4. On the phone, open `http://<computer's Tailscale name>:8501`.

Don't expose it to the open internet, because it shows order IDs.

## 7. Optional data

- **Units sold** (for return % and selling %): add a `tabs.sales` section. There's an example in `config.example.yaml`. `doctor` shows it as optional until it's configured.
- **Scale returns:** when a scales return sheet exists, point `tabs:` at it. Scale products are already in the product list.
- **More listings:** `uv run cultph amazon-discover "cult <product>"` and `uv run cultph flipkart-discover "cult <product>"` find listing IDs. Add them under `amazon.asins` or `flipkart.listings`.

## Troubleshooting

| Symptom | Meaning / fix |
|---|---|
| The dashboard sidebar says **BLOCKED** | A data check failed, so the previous good data stays on screen. The sidebar lists the failing check and the sheet row it points to. |
| `captcha` in the Amazon log | Amazon is rate-limiting. The run stops for that cycle and the next run retries. |
| "selectors may be stale" / "layout changed" | Amazon or Flipkart changed their page layout. The page is saved under `data/raw/` so the parser can be updated. |
| "Google login expired" | Run `uv run cultph sheets` again, or use Option B, or publish the OAuth app (step 4). |
| An AI label looks wrong | Fix it in **Review issues → Review queue**. Your fixes build the accuracy score. |
