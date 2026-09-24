# Cult Product Health: Plan

A web app for Cult's Massagers and Scales business. It watches every SKU on e-commerce platforms (Amazon first), reads every review, links reviews to the return and exchange data kept in Google Sheets, and shows insights where each number can be traced back to the source rows.

---

## 0. Build decisions (personal-use version)

This is being built for personal use, so it is deliberately simple.

- **Stack:**
  - Python 3.12 managed with uv, with a single **SQLite** file under `data/`.
  - A **Streamlit** dashboard, and a CLI (`cultph sync`).
  - This replaces the Next.js, Postgres and Auth.js stack in §3, which can come later if it ever needs to be multi-user.
- **Google Sheets:** `gspread` with desktop OAuth. The Gmail login happens once and the token is cached locally. The xlsx reader and the Sheets reader return the same table shape.
- **Amazon reviews:** our **own parser** (Playwright). This is option D in §5, which is acceptable for personal, low-volume use. It polls gently (about hourly with jitter), and any login session is stored under `data/`, never committed.
- **Public repo hygiene:** real tab and column names and the SKU master live in `config.private.yaml` (gitignored). The repo ships `config.example.yaml` and a synthetic fixture instead.
- **Publish gate:** a sync writes to a staging DB. It is swapped in as the live DB only if the judge doesn't fail it.

### Amazon spike findings (amazon.in, Sep 2026)
- **Product page, no login:**
  - It shows the displayed average (1 decimal), the exact global ratings count, a star histogram of *rounded percentages*, and about 8 top reviews with full text, review ID, stars, date, variant, verified badge and helpful votes. Headless Chromium had no captcha at about 1 request every 4–9 seconds.
  - **The histogram is Amazon's weighted distribution, not raw counts.** For example, a listing with 6 ratings showed 64/0/15/21/0%. The displayed average matches the histogram's weighted mean to within about 0.02. So the 4.1 calculator works on that weighted mean, and gives a range to account for the rounding.
  - **Amazon displays one decimal,** so "shows 4.1" means a weighted mean of 4.05 or more. This is configurable with `target_mode`.
  - **Variation families share one rating pool** (the same `parentAsin`). Several ASINs can show identical ratings, so the dashboard groups them.
  - Amazon sometimes serves an alternate page layout with no ratings block. The poller retries, and the judge never stores a page that fails its checks.
- **Review listing (`/product-reviews/`):** redirects to sign-in when logged out. With a saved login, the poller reads "most recent" until it reaches reviews it already has. `--backfill` walks every star filter. The page cap for each filter still needs checking once logged in.

### Flipkart findings (Sep 2026)
- **No login and no bot wall.** The product page has schema.org JSON-LD with the rating value, rating count, review count and a link to the reviews page.
- **The reviews page (sorted by latest) shows *exact* per-star counts,** so the 4.1-style math on Flipkart is exact rather than a range. The judge checks that the star counts add up to the ratings total.
- The page is React Native Web, with no stable class names and no review IDs. Reviews are parsed from the visible text pattern, and each gets a stable ID made by hashing the listing, reviewer, city, title and body (reviewer names are not stored). Dates are relative ("3 days ago", "2 months ago"), so each review records how precise its date is.
- Flipkart's displayed rating equals the exact mean rounded to one decimal on every listing checked, so the "shows 4.1 means a mean of 4.05 or more" rule holds there too.
- Volume observed: about 160 new Flipkart reviews a month across the listings (Volt and Revive the busiest). The AI labeller is capped at `ai.max_per_run` reviews per run (default 200), taking the lowest ratings and newest first, so a backlog after a backfill clears over several runs.
- Some listings occasionally render one review repeated. Repeats are de-duplicated, and if page 1 yields fewer than 5 distinct reviews while the header says 10 or more, the run warns and saves the page.

### Phase status
| Phase | Status |
|---|---|
| 1. Sheets and returns: parser, SQLite, judge, dashboard | ✅ done. The real workbook reconciles exactly with the sheet's own Dashboard across all months; 0 rows rejected; 100% of named models mapped |
| 2. Amazon ratings and reviews (own parser) | ✅ product-page poller, append-only review store, scrape judge, 4.1 calculator, dashboard tab. The full review listing needs a one-time login (`cultph amazon-login`) |
| 3. AI review classification with a judge | ✅ built and tested with a fake model client. `cultph label` needs `ANTHROPIC_API_KEY`. Haiku labels each review; code checks the evidence quotes and the codes; Sonnet judges; disagreements go to a person in the dashboard queue, which also builds the gold set |
| 5. Handover / unattended setup | ✅ `cultph setup` and `cultph doctor [--live]`; Flipkart poller; sales-based return % and selling %; massager/scale category; cross-platform `watch`; optional dashboard password; SETUP.md and a private handover bundle |
| 4. Alerts and scheduling | ✅ `cultph run` (sync → Amazon → label → alerts); rules for new 1–2★ reviews, safety labels, rating changes, crossing the target, and weekly return spikes; the first run only records a baseline; each alert is sent once; macOS notifications by default, plus Telegram/Slack/email when configured; `cultph schedule` writes a launchd plist (not installed) |

---

## 1. Goals (from the brief)

| # | Requirement | How this plan covers it |
|---|---|---|
| G1 | Track all Massager and Scale SKUs on e-commerce (Amazon first) | SKU master plus a per-platform listing map (ASIN, FSN, etc.) |
| G2 | Real-time data and review counts | Scheduled pollers (every 15–60 min per SKU) plus a live dashboard |
| G3 | Read every review one by one and find product problems | Each review is classified individually into an issue taxonomy, with evidence stored |
| G4 | Works anywhere | A responsive web app (mobile and desktop) with Google login |
| G5 | Detailed insights with backing data you can cross-check | Every metric and insight has a drill-down to the exact reviews and sheet rows behind it |
| G6 | Real-time review alerts | Rules engine, with alerts by email, Slack or WhatsApp, and in-app |
| G7 | Use the return and exchange data from Google Sheets | Google OAuth; the user picks sheets shared with them; the ingest is incremental |
| G8 | Return %, cause, selling %, issue %, split out separately | Metrics layer broken down by SKU × platform × month × issue |
| G9 | "100% accurate" | Deterministic metrics are reconciled exactly; AI outputs go through a judge and a human-review queue (see §6) |
| G10 | Proofread itself before showing results | A judge step blocks any publish that fails its checks |
| G11 | How many ratings are needed to hold Amazon at 4.1 or above | Rating calculator per ASIN (see §7) |

---

## 2. What the sample return data shows

The return and exchange data comes from a Google Sheets workbook that is updated by raising tickets. It holds several months of data in four raw tabs, plus pivot tabs made by hand.

| Tab (role) | What it is | Typical fields |
|---|---|---|
| **Tickets** | Customer support tickets | ticket id, issue levels (L1/L2/L3), created time, platform, order id, model, subject |
| **Approved returns/exchanges** | Returns and exchanges that were approved | date, order id, batch, model, SKU, amount, issue, mode (return/exchange), platform, warehouse |
| **Pending verification** | Returns not yet verified | order id, SKU, model, created at, return reason, customer comment |
| **Warehouse returns** | WMS return records | channel, return type (RTO/RVP/exchange), reason, order id, SKU, facility, status |
| Dashboard (derived) | Pivots made by hand | monthly totals (approved vs total) |

### Findings that shape the design

1. **There is a built-in reconciliation check.** The Dashboard's approved total equals the row count of the approved-returns tab. The judge step will extend this idea: recompute every Dashboard number from the raw tabs, and fail the publish if they don't match.
2. **The same product has a different name in each tab**, for example a short model name in one tab and a longer descriptive name in another.
   - SKU codes appear in some tabs but not in the tickets tab.
   - A large share of tickets have no model named at all.
   - **Decision:** keep a canonical SKU master and an alias table. Any name that isn't mapped goes to a review queue. There is **no silent fuzzy matching**, because a wrong mapping would corrupt every metric.
3. **Headers and values are dirty.** Headers have typos and trailing spaces. The same missing value is written in more than one way. Months use a custom text format. Order ID formats differ by platform, and some cells hold more than one tracking number. The ingest layer trims and normalises all of this and logs every change it makes.
4. **Order ID formats tell us the platform** (Amazon, Flipkart and the Cult store each use a different pattern). This lets us fill in the platform where that column is missing.
5. **Gaps that need input from Cult:**
   - **There is no Scales data in the sample.** We need the equivalent sheet for Scales.
   - **There is no sales denominator.** Return % and selling % need units sold per SKU × platform × month. Candidate sources: the Amazon SP-API *Sales & Traffic* report (Seller Central), the Vendor Central retail analytics, or an internal sales sheet.
6. **The sheet contains PII** (customer emails, order IDs, tracking numbers). Raw data is never committed to git. The app stores emails hashed, or doesn't store them at all.

---

## 3. Architecture

```
            ┌──────────────── Sources ────────────────┐
            │ Google Sheets   Amazon reviews   Other   │
            │ (OAuth, user's  (see §5)         platforms│
            │  shared sheets)                  (later)  │
            └──────┬──────────────┬───────────────┬────┘
                   ▼              ▼               ▼
            ┌─────────────── Ingest workers ───────────┐
            │ fetch → normalise → map to SKU → dedupe   │
            │ every raw row keeps source + row pointer  │
            └──────────────────┬────────────────────────┘
                               ▼
            ┌──────────── Postgres (raw + clean) ───────┐
            └──────┬───────────────────────┬────────────┘
                   ▼                       ▼
         ┌── AI pipeline ──┐      ┌── Metrics engine ──┐
         │ classify review │      │ deterministic SQL  │
         │ → judge model   │      │ (return %, issue % │
         │ → confidence    │      │  rating math …)    │
         │ → human queue   │      └─────────┬──────────┘
         └───────┬─────────┘                │
                 ▼                          ▼
            ┌──────────── Judge / publish gate ─────────┐
            │ reconciliation checks + AI verdict checks │
            │ pass → publish snapshot; fail → block+alert│
            └──────────────────┬────────────────────────┘
                               ▼
            ┌──── Web app (Next.js) ────┐   ┌── Alerts ──┐
            │ dashboard, drill-downs,   │   │ email/Slack│
            │ review queue, calculator  │   │ /WhatsApp  │
            └───────────────────────────┘   └────────────┘
```

### Proposed stack
- **Web:** Next.js (App Router) with TypeScript, Tailwind and shadcn/ui, and Recharts or Tremor for charts.
- **Auth:** Auth.js with a Google provider. Scopes: `openid email profile`, `spreadsheets.readonly`, `drive.metadata.readonly` (so the app can list the sheets shared with the user). We store the refresh token so background syncs can run.
- **DB:** Postgres (Supabase or Neon) with Drizzle ORM.
- **Workers and scheduling:** a queue-backed worker (Inngest, Trigger.dev, or a simple cron plus a Postgres job table). Each poller is idempotent.
- **AI:** Claude API. A fast model (Haiku 4.5) classifies each review. A stronger model (Sonnet 5) acts as the judge. Outputs use structured JSON schemas.
- **Alerts:** Resend (email), a Slack webhook, and optionally WhatsApp through a BSP.
- **Hosting:** Vercel for the web app and Railway or Fly for the workers, or everything on Railway.

> **Google OAuth note:** `spreadsheets.readonly` is a sensitive scope. Until Google verifies the app, it runs in "Testing" mode for up to 100 named test users. That is fine for internal use at Cult.

---

## 4. Data model (core tables)

```
sku               (id, sku_code, canonical_name, category[massager|scale], subcategory, launch_date, active)
sku_alias         (alias_text, source, sku_id, approved_by, approved_at)      -- name → SKU mapping
listing           (id, sku_id, platform, external_id[ASIN/FSN…], url, marketplace)
unmapped_name     (alias_text, source, first_seen, occurrences)               -- review queue

sheet_source      (id, user_id, spreadsheet_id, tab, role[o2c|rr|pending|wms|sales], last_synced_at, header_map_json)
raw_row           (id, sheet_source_id, row_number, row_hash, payload_json, ingested_at)   -- immutable
ticket            (id, raw_row_id, ticket_id, created_at, platform, order_id, sku_id, l1, l2, l3, ...)
return_event      (id, raw_row_id, source_tab, event_date, order_id, sku_id, mode[return|exchange|rto|rvp],
                   platform, channel, reason_raw, issue_code, amount, batch, warehouse, status)
sales_fact        (sku_id, platform, period, units_sold, revenue, source)

review            (id, listing_id, platform_review_id, rating, title, body, author_hash, review_date,
                   verified_purchase, variant, helpful_votes, fetched_at, first_seen_at, source)
rating_snapshot   (listing_id, captured_at, displayed_avg, total_ratings, star_1..star_5)
review_label      (review_id, issue_codes[], sentiment, severity, evidence_quote, classifier_model,
                   classifier_conf, judge_model, judge_verdict, judge_notes, human_verdict, final)
issue_taxonomy    (code, name, parent, description)     -- aligned to the issue levels in the sheet

metric_snapshot   (id, run_id, metric, dims_json, value, numerator, denominator, source_query_hash)
publish_run       (id, started_at, status[passed|blocked], checks_json)
alert_rule / alert_event
```

**Traceability rule:** every derived row keeps a pointer back to its source (a `raw_row_id` or a `review_id`). Every metric stores both its numerator and denominator. The UI's "show me the data" drawer uses these pointers.

**Issue taxonomy:** it starts from the sheet's own issue levels, for example device dead, charging faults, noise or vibration, heat, buttons, battery drain, and damage on arrival. Reviews and returns then use one shared vocabulary, so "issue % in reviews" and "issue % in returns" can be compared directly.

---

## 5. Amazon reviews: a decision Cult needs to make

**The official Amazon SP-API does not return review text.** The real options depend on how Cult sells on Amazon.

| Option | Coverage | Risk | Notes |
|---|---|---|---|
| A. **Brand Registry / Seller Central "Customer Reviews"** | Reviews on Cult's own brand ASINs | Low (it's Cult's own account) | Mostly a UI and export, with no public API. We may need a scheduled export or an authorised browser automation. **Needs checking against Cult's account.** |
| B. **Vendor Central** (if Cult is a 1P vendor) | Varies | Low | Check which review and analytics reports are available. |
| C. **Third-party review data APIs** (for example Rainforest/Traject, Oxylabs, Bright Data, ScraperAPI, Apify actors) | All public reviews, including competitors | Medium (Amazon ToS, cost per call) | Coverage, freshness and pricing **must be tested** during a paid trial before we commit. |
| D. **Own scraper** | Public reviews | High (Amazon login walls, blocking, ToS) | Not recommended. |

**Recommendation:** pull `rating_snapshot` (the displayed average plus the star histogram) and the review text through a pluggable `ReviewSource` interface, so we can switch providers. Start with whichever of A or C passes a one-week accuracy test. The test: compare our fetched review count and star histogram against what Amazon displays for 5 ASINs, every day.

**"Real time" means:** poll each ASIN every 15–60 minutes (configurable, bounded by cost). Amazon offers no push notification for new reviews. An alert fires within one polling cycle of a review appearing.

Other platforms (Flipkart, Myntra, CRED, Zepto, Cult store) plug into the same interface later.

---

## 6. Accuracy: the judge step and what "100%" can honestly mean

There are two kinds of output, and each gets its own guarantee.

### 6a. Deterministic metrics: exact and reconciled
Return %, issue %, selling %, counts and rating math are **plain SQL over the stored rows**. They are 100% reproducible. Before any publish, the **judge gate** runs checks like these:

- **Row conservation:** rows read from the sheet = rows stored + rows rejected, and every rejection has a logged reason.
- **Dashboard reconciliation:** recompute the sheet's own Dashboard numbers (for example approved per month = the approved-returns row count per month) and fail on any difference.
- **Segment sums:** issue percentages per SKU add up to 100%. The SKU totals add up to the platform total, which adds up to the grand total.
- **Denominator sanity:** return % ≤ 100%. There are no divide-by-zero values. The sales period matches the return period.
- **Mapping coverage:** the % of rows mapped to a SKU is shown next to every metric. The publish is blocked if the unmapped share goes over a threshold (for example 2%, excluding rows that are genuinely "Not Mentioned").
- **Review count check:** the stored review count and star histogram for each ASIN match the latest `rating_snapshot` (within the provider's known lag).
- **Diff vs last run:** any metric that moves by more than X% is flagged for a person to look at before it's published.

If any check fails, the run is **blocked**. The last good snapshot stays live, and an alert says which check failed.

### 6b. AI review classification: judged, measured, reviewable
An LLM cannot be 100% accurate. So we make it **checkable** instead:

1. **The classifier** (Haiku) reads each review **one at a time**. It returns issue codes, sentiment, severity, and an **exact quote from the review** that supports each label.
2. **Automatic checks:** the quote must appear word for word in the review text, and the issue codes must exist in the taxonomy.
3. **The judge** (Sonnet) independently checks the review against the proposed labels. It returns agree, disagree or unsure, with a reason.
4. **Routing:** a label becomes final automatically only if the classifier's confidence is above the threshold **and** the judge agrees. Everything else goes to a **human review queue** in the app.
5. **Measurement:** a gold set of about 300 reviews labelled by people. The precision and recall for each issue are shown on the dashboard, so everyone knows how far to trust the AI numbers. The gold set is re-run whenever a prompt or model changes.
6. **Evidence in the UI:** every AI insight (for example "Cult Impact X: 34% of negative reviews mention charging failure") links to the full list of those reviews, with the evidence quote highlighted.

---

## 7. The Amazon 4.1 rating calculator

For each ASIN, where **N** is the total number of global ratings (every star rating, not just written reviews), **S** is the sum of all stars, and **T** is the target (4.1):

- **Current simple average:** `A = S / N`
- **5★ ratings needed to reach T** (if A < T): `k = ceil((T·N − S) / (5 − T)) = ceil((4.1·N − S) / 0.9)`
- **1★ ratings it can absorb while staying ≥ T** (if A ≥ T): `m = floor((S − T·N) / (T − 1)) = floor((S − 4.1·N) / 3.1)`
- **Run-rate view:** given the recent mix of incoming ratings, show the projected average in 30 and 90 days, and the share of 5★ ratings needed to hold 4.1.

> ⚠ Amazon's *displayed* rating is a weighted model (recency, verified purchase, and so on), not a simple average. The calculator gives an **estimate**. We track the gap between our simple average and the displayed value over time, and show it next to the recommendation so nobody treats the estimate as exact.

---

## 8. Dashboard (key screens)

1. **Overview:** the portfolio at a glance for each SKU: rating, rating trend, review volume, return %, top issue, and alert badges.
2. **SKU detail:** the rating histogram and trend, the 4.1 calculator, a Pareto chart of issues (reviews vs returns side by side), and the return/exchange split by platform and month.
3. **Returns and exchanges:** return %, exchange %, RTO vs customer returns, and causes. Each figure is split separately by SKU, platform, month, warehouse and batch (batch matters, because a spike in one batch points to a manufacturing problem).
4. **Reviews feed:** a live stream of reviews with filters (stars, issue, SKU, platform). Each one shows its label, the judge's verdict and the evidence.
5. **"Show me the data" drawer:** available on every number. It shows the numerator rows, the denominator rows, the source sheet and row number, and a CSV export.
6. **Review queue:** unmapped model names and uncertain AI labels, both handled by people.
7. **Pipeline health:** sync status for each source, the judge results for each run, and the reason any publish was blocked.
8. **Alerts setup.**

### Alert rules (examples)
- A new 1★ or 2★ review on any tracked ASIN.
- Any review mentioning a safety issue (heat, burning, shock, battery swelling), sent at the highest priority.
- An issue's share rises more than X percentage points week on week for a SKU.
- The Amazon average is projected to fall below 4.1 within N days, or is already below it.
- A spike in returns for one SKU, platform or batch compared with the trailing 4-week average.

---

## 9. Phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **0. Discovery (this week)** | Confirm the Amazon account type (Seller/Vendor, Brand Registry), the list of ASINs, the source for sales data, and the Scales sheet. Trial one review data source. | Each open question in §10 has an answer |
| **1. Sheets and returns** | Google login, sheet picker, ingest for all 4 tabs, SKU master and alias queue, return/exchange metrics, the judge reconciliation gate, drill-downs | Reproduces the sheet's Dashboard exactly; 100% of rows accounted for |
| **2. Amazon ratings and reviews** | ReviewSource adapter, rating snapshots, review ingest, the 4.1 calculator | Review counts and histogram match Amazon for every tracked ASIN |
| **3. AI pipeline** | Per-review classifier, judge, human queue, gold set and accuracy panel | Measured precision of at least 90% on the gold set for the top 8 issues |
| **4. Alerts and real time** | Pollers on schedules, the rules engine, email and Slack | An alert arrives within one polling cycle of a new review |
| **5. Joined insights** | Reviews × returns × tickets per SKU, batch analysis, Flipkart and other platforms | n/a |

---

## 10. Open questions for Cult

1. Is Cult on Amazon **Seller Central (with Brand Registry)** or **Vendor Central**? Who can grant API or export access?
2. Where is the complete **ASIN / FSN list** for every Massager and Scale SKU?
3. What is the **source for units sold** per SKU × platform × month (needed for return % and selling %)?
4. Where is the equivalent return sheet for **Scales**?
5. Should "return %" mean approved returns only, or include Pending verification and WMS/RTO rows? (We recommend showing all three as separate layers.)
6. What is the budget for a third-party review API, if one is needed?
7. Who handles alerts, and on which channel (email, Slack, WhatsApp)?
8. Are the sheet tabs and headers stable month to month, or do they change? (The ingest has a header map that can be edited in the app either way.)
9. Where should it be hosted, and are there data-residency rules for customer data?
