# Setup guide

This guide takes a fresh computer to a running dashboard. Everything except the accounts and keys is already built. Run `uv run cultph doctor` at any point to see what's done and what to do next.

## 1. Install (about 5 minutes)

```bash
git clone https://github.com/akashub/cult-product-health.git
cd cult-product-health
```

Unzip the handover zip you received privately into this folder (keep its paths). It contains `config.private.yaml` and `PLAN.private.md` (the Cult product list and sheet layout, never stored on GitHub). If it was made with `--with-data`, it also contains `data/amazon.db`: the reviews, ratings, photos, AI labels and trend history collected so far, so the dashboard isn't empty on day one.

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

## 4. Getting the returns data in (two ways)

Open the dashboard (`uv run streamlit run app/dashboard.py`) and go to the **Data sources** tab.

**A. Upload a workbook (always works).** In Google Sheets choose File → Download → Microsoft Excel (.xlsx), then drop the file in "Upload a workbook" and click **Check & import**.
- Every row is checked before anything is shown.
- Tabs are recognised by their columns, so renamed tabs or a new layout next to the old one are fine.
- The file is read and then deleted. Customer emails are never stored.

**B. Import straight from Google Sheets with your Cult Gmail.**
1. One-time setup on this computer:
   - At console.cloud.google.com, create a project and enable the **Google Sheets API** and the **Google Drive API**.
   - Configure the OAuth consent screen: choose *External*, add your Cult Gmail as a test user, then click **Publish app**. Publishing stops Google from expiring the sign-in every 7 days; you'll see an "unverified app" warning, which is fine for personal use.
   - Under Credentials → Create credentials → OAuth client ID, choose **Desktop app**. Download the JSON and save it as `data/google_client.json`.
2. In **Data sources → Import from Google Sheets**, paste the sheet link and click **Sign in & import**. A Google window opens the first time; sign in with the Cult account the sheet is shared with. The sign-in is remembered on this computer.
3. Leave **Use this sheet for scheduled syncs** ticked, and `cultph run` / `cultph watch` will then read that sheet automatically.

> If Google says *"Access blocked"* or *"admin policy"*, Cult's Workspace admin doesn't allow outside apps to read company Sheets. Use option A (upload) instead, or ask IT to allow the app.

You can also do this from the terminal: `uv run cultph sheets` lists the sheets your account can see, and `uv run cultph sync` imports the configured one.

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

### Open the dashboard from your phone (optional)
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
