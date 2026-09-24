# Cult Product Health

A personal dashboard and pipeline for Cult Massagers and Scales. It covers return and exchange data from Google Sheets, Amazon ratings and reviews, and issue analysis. Every number can be traced back to its source row.

See [PLAN.md](./PLAN.md) for the full plan and the phase status.

## Quick start

```bash
uv sync
cp config.example.yaml config.private.yaml   # fill in real tab names, headers, SKU master
uv run cultph sync                           # parse → judge → publish (blocked if any check fails)
uv run streamlit run app/dashboard.py        # open the dashboard
uv run pytest                                # tests (synthetic fixture; real-data test runs only locally)

# Amazon (own parser, Playwright)
uv run playwright install chromium           # once
uv run cultph amazon-discover "cult massage gun"   # find ASINs, then add them under amazon.asins
uv run cultph amazon-login                   # once, in your own terminal: sign in (use a secondary account)
uv run cultph amazon                         # poll: ratings, histogram, new reviews
uv run cultph amazon --backfill              # walk every star filter (needs login)

# AI issue labels (classifier + judge)
echo 'ANTHROPIC_API_KEY=sk-ant-...' > data/.env
uv run cultph label                          # labels new/changed reviews one by one
```

To try it without real data, run `uv run python tests/make_fixture.py` and then `uv run cultph --config config.example.yaml sync`.

### Reading directly from Google Sheets (Gmail login)
1. In Google Cloud Console, create a project and enable the **Google Sheets API** and the **Google Drive API**.
2. Create an OAuth client of type **Desktop app**, download its JSON, and save it as `data/google_client.json`.
3. Run `uv run cultph sheets`. A browser opens so you can sign in with the Gmail account the sheets are shared with. Then it lists the spreadsheet IDs.
4. In `config.private.yaml`, set `source: {type: gsheet, spreadsheet_id: <id>}` and run `uv run cultph sync`.

## How accuracy is enforced
Each `sync` runs the judge (`src/cultph/judge.py`) before publishing. Its checks:
- **Row conservation:** every non-blank sheet row is either stored or rejected with a reason.
- **Clean IDs:** no `123.0` float artifacts and no scientific notation.
- **Month labels:** each row's month label agrees with its date.
- **Product mapping:** coverage is measured. SKU/name conflicts are flagged, and nothing is fuzzy-matched.
- **Segment sums:** issue shares add up to 100% within each product.
- **Dashboard reconciliation:** the sheet's own Dashboard numbers are recomputed from the raw tabs, and must match exactly.

If any check fails, the new data is **not** published and the previous snapshot stays live. Every table in the dashboard has a drill-down to the exact sheet tab and row.

> **Data policy:** raw sheets, `config.private.yaml`, `data/` (the database, Google tokens, scraper sessions) and `*.private.md` are gitignored. They contain customer PII or Cult-internal details.
