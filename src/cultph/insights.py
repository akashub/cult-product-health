"""Deterministic product-health insights, bi-weekly review trends and the
product scorecard. Every insight carries its numbers, window, sample size and
a pointer to where the evidence lives, so it can be cross-checked.

Correctness rules (why trends can be trusted):
- Only unbiased reviews count: Amazon's 'most recent' walk and Flipkart's
  latest-sorted pages with day-precise dates. Product-page "top reviews" and
  star-filtered backfills are skewed samples and are excluded.
- A listing whose recent walk hit its cap only has complete data back to its
  oldest walked review; older periods are marked incomplete, never shown as a decline.
- The unit is the product, or the rating pool when several products share one.
- Trend flags need a minimum number of reviews in both windows.
- Issue mixes use <=3 star reviews only (the labeller prioritised those) and
  report label coverage; issue spikes are suppressed below MIN_LABEL_COVERAGE."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

WINDOW = 14
MIN_N = 8                     # reviews needed in each window before flagging a trend
STAR_DROP = 0.3               # avg-star drop that counts as worsening
NEG_RISE = 0.10               # rise in 1-2 star share that counts as worsening
MIN_LABEL_COVERAGE = 0.70
CAPS = {"amazon": 100, "flipkart": 50}
CAP_SLACK = 10               # up to ~8 walked reviews were already stored from the product page
SEVERITY_ORDER = {"act": 0, "watch": 1, "info": 2, "good": 3}


@dataclass
class Insight:
    severity: str            # act | watch | info | good
    title: str
    detail: str
    where: str               # pointer to the dashboard place holding the evidence
    evidence: pd.DataFrame | None = None
    key: str = ""
    metric: str = ""         # short headline number for the card, e.g. "10/14"
    metric_label: str = ""

    def as_dict(self) -> dict:
        return {"severity": self.severity, "title": self.title, "detail": self.detail, "where": self.where}


@dataclass
class Inputs:
    reviews: pd.DataFrame
    labels: pd.DataFrame
    snaps: pd.DataFrame
    approved: pd.DataFrame = field(default_factory=pd.DataFrame)
    target_shown: float = 4.1
    target: float = 4.05
    last_sync_at: str | None = None
    has_ai_key: bool = False


# ---------------------------------------------------------------- loading

def _read(db: Path, sql: str) -> pd.DataFrame:
    if not db.exists():
        return pd.DataFrame()
    with sqlite3.connect(db) as con:
        try:
            return pd.read_sql(sql, con)
        except Exception:  # noqa: BLE001 - table not there yet
            return pd.DataFrame()


def load_inputs(amazon_db: Path, live_db: Path, cfg, run_log: list[dict] | None = None,
                has_ai_key: bool = False) -> Inputs:
    from .amazon.rating import effective_target

    amz = cfg.raw.get("amazon", {})
    shown = float(amz.get("target_rating", 4.1))
    reviews = _read(amazon_db, "SELECT review_id, asin, parent_asin, product, platform, rating, title, body, "
                               "review_date, date_precision, source, first_seen_at FROM review")
    labels = _read(amazon_db, "SELECT review_id, codes, severity, status, human_verdict, human_codes, issues "
                              "FROM review_label")
    snaps = _read(amazon_db, "SELECT * FROM rating_snapshot")
    approved = _read(live_db, "SELECT product, category, event_date, month, issue, mode FROM approved")
    last_sync = next((r["at"] for r in (run_log or []) if r.get("published")), None)
    return Inputs(reviews, labels, snaps, approved, shown, effective_target(shown, amz.get("target_mode", "displayed")),
                  last_sync, has_ai_key)


# ---------------------------------------------------------------- building blocks

def latest_snapshots(snaps: pd.DataFrame) -> pd.DataFrame:
    if snaps.empty:
        return snaps
    s = snaps.copy()
    s["platform"] = s.get("platform", pd.Series(["amazon"] * len(s))).fillna("amazon")
    s["pool"] = s["parent_asin"].fillna(s["asin"])
    return s.sort_values("captured_at").groupby("asin").tail(1)


def pool_labels(latest: pd.DataFrame) -> dict[str, str]:
    """pool id -> readable name ('Cult Impact / Cult Flex'); duplicate names get a short id suffix."""
    out: dict[str, str] = {}
    for (plat, pool), g in latest.groupby(["platform", "pool"]):
        out[pool] = " / ".join(sorted(set(g["product"].dropna())))
    seen: dict[tuple, int] = {}
    for (plat, pool), _ in latest.groupby(["platform", "pool"]):
        seen[(plat, out[pool])] = seen.get((plat, out[pool]), 0) + 1
    for (plat, pool), _ in latest.groupby(["platform", "pool"]):
        if seen[(plat, out[pool])] > 1:
            out[pool] = f"{out[pool]} ·{pool[-4:]}"
    return out


def unbiased_reviews(reviews: pd.DataFrame, labels_by_pool: dict[str, str]) -> pd.DataFrame:
    if reviews.empty:
        return reviews.assign(unit=[], day=[])
    r = reviews.copy()
    r["platform"] = r["platform"].fillna("amazon")
    src = r["source"].fillna("")
    keep = ((r["platform"] == "amazon") & (src == "listing:recent:all")) | \
           ((r["platform"] == "flipkart") & src.str.startswith("flipkart:recent") &
            (r["date_precision"].fillna("day") == "day"))
    r = r[keep & r["review_date"].notna()].copy()
    r["pool"] = r["parent_asin"].fillna(r["asin"])
    r["unit"] = r["product"].where(r["product"].notna(), r["pool"].map(labels_by_pool))
    r["unit"] = r["unit"].fillna(r["pool"])
    r["day"] = pd.to_datetime(r["review_date"]).dt.date
    return r


def coverage_cutoffs(unb: pd.DataFrame) -> dict[tuple[str, str], date]:
    """(platform, unit) -> earliest date with complete data, for capped listings."""
    out: dict[tuple[str, str], date] = {}
    for (plat, pool), g in unb.groupby(["platform", "pool"]):
        if len(g) >= CAPS.get(plat, 100) - CAP_SLACK:
            cut = g["day"].min()
            for unit in g["unit"].unique():
                key = (plat, unit)
                out[key] = max(out.get(key, cut), cut)
    return out


def windows(today: date, n: int = 8) -> list[tuple[date, date]]:
    """Rolling 14-day windows ending today, newest first: [(start, end), ...] inclusive."""
    return [(today - timedelta(days=WINDOW * (k + 1) - 1), today - timedelta(days=WINDOW * k)) for k in range(n)]


def review_trends(unb: pd.DataFrame, today: date, n_windows: int = 8) -> pd.DataFrame:
    """One row per platform x unit x window: n, avg stars, share of 1-2 star, completeness."""
    cuts = coverage_cutoffs(unb)
    rows = []
    for (plat, unit), g in unb.groupby(["platform", "unit"]):
        cut = cuts.get((plat, unit))
        for k, (start, end) in enumerate(windows(today, n_windows)):
            w = g[(g["day"] >= start) & (g["day"] <= end)]
            complete = cut is None or start >= cut
            rows.append({"platform": plat, "unit": unit, "window": k, "start": start, "end": end,
                         "label": f"{start:%d %b}–{end:%d %b}", "n": len(w),
                         "avg_stars": w["rating"].mean() if len(w) else None,
                         "neg_share": (w["rating"] <= 2).mean() if len(w) else None,
                         "complete": complete, "latest": k == 0})
    return pd.DataFrame(rows)


def final_labels(labels: pd.DataFrame) -> pd.DataFrame:
    if labels.empty:
        return labels.assign(final_codes=[])
    lab = labels.copy()
    final = lab["status"].eq("auto") | lab["human_verdict"].isin(["correct", "fixed"])
    lab["final_codes"] = lab["codes"].where(lab["human_verdict"] != "fixed", lab["human_codes"]).fillna("")
    return lab[final]


def issue_mix(unb: pd.DataFrame, labels: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Issue counts among <=3 star unbiased reviews in [start, end], with label coverage per unit."""
    low = unb[(unb["rating"] <= 3) & (unb["day"] >= start) & (unb["day"] <= end)]
    fin = final_labels(labels)
    merged = low.merge(fin[["review_id", "final_codes"]], on="review_id", how="left")
    rows = []
    for (plat, unit), g in merged.groupby(["platform", "unit"]):
        labelled = g["final_codes"].notna()
        if not labelled.any():
            continue
        codes = g.loc[labelled, "final_codes"].astype(str).str.split(",").explode()
        codes = codes[codes.fillna("") != ""]
        for code, cnt in codes.value_counts().items():
            rows.append({"platform": plat, "unit": unit, "code": code, "reviews": int(cnt),
                         "labelled": int(labelled.sum()), "low_reviews": len(g),
                         "coverage": labelled.mean() if len(g) else 0.0})
    return pd.DataFrame(rows, columns=["platform", "unit", "code", "reviews", "labelled", "low_reviews", "coverage"])


def daily_snapshots(snaps: pd.DataFrame) -> pd.DataFrame:
    if snaps.empty:
        return snaps
    s = snaps.copy()
    s["day"] = pd.to_datetime(s["captured_at"]).dt.date
    return s.sort_values("captured_at").groupby(["asin", "day"]).tail(1)


def snapshot_change(snaps: pd.DataFrame, today: date, days: int = WINDOW) -> pd.DataFrame:
    """Per listing: now vs the last snapshot on or before today-days (if history reaches that far)."""
    d = daily_snapshots(snaps)
    rows = []
    for asin, g in d.groupby("asin"):
        g = g.sort_values("day")
        now_ = g.iloc[-1]
        old = g[g["day"] <= today - timedelta(days=days)]
        base = old.iloc[-1] if len(old) else None
        rows.append({"asin": asin, "since": g["day"].min(), "has_history": base is not None,
                     "rating_now": now_["avg_rating"], "rating_then": None if base is None else base["avg_rating"],
                     "ratings_added": None if base is None else int(now_["total_ratings"] - base["total_ratings"]),
                     "bought_now": now_.get("bought_min"), "bought_then": None if base is None else base.get("bought_min"),
                     "bsr_sub_now": now_.get("bsr_sub"), "bsr_sub_then": None if base is None else base.get("bsr_sub")})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- scorecard

def scorecard(inp: Inputs, today: date) -> pd.DataFrame:
    """One row per product and platform: rating vs target, sales proxy, rank, price,
    last-14-day review stats vs the previous 14 days, top complaint, safety flags."""
    from .amazon.rating import plan, plan_exact

    latest = latest_snapshots(inp.snaps)
    if latest.empty:
        return pd.DataFrame()
    names = pool_labels(latest)
    unb = unbiased_reviews(inp.reviews, names)
    trends = review_trends(unb, today, 2)
    mix = issue_mix(unb, inp.labels, today - timedelta(days=29), today)
    safety = _safety(inp, today, 30)
    latest = latest.assign(unit=latest["pool"].map(names))
    rows = []
    for (plat, unit), g in latest.groupby(["platform", "unit"]):
        top = g.sort_values("total_ratings").iloc[-1]          # the listing with the most ratings speaks for the unit
        if plat == "flipkart" and pd.notna(top.get("c5")):
            pl = plan_exact({k: int(top[f"c{k}"]) for k in range(1, 6)}, inp.target)
        else:
            pl = plan(int(top["total_ratings"]), {k: top[f"p{k}"] for k in range(1, 6)}, inp.target, shown=top["avg_rating"])
        need = pl["five_star_needed"]
        t = trends[(trends["platform"] == plat) & (trends["unit"] == unit)] if not trends.empty else trends
        cur = t[t["window"] == 0].iloc[0] if len(t) else None
        prev = t[t["window"] == 1].iloc[0] if len(t) > 1 else None
        m = mix[(mix["platform"] == plat) & (mix["unit"] == unit)] if not mix.empty else mix
        top_issue = f"{m.iloc[0]['code']} ({m.iloc[0]['reviews']})" if len(m) else ""
        rows.append({
            "platform": plat, "product": unit, "listings": len(g),
            "rating": top["avg_rating"] if pd.notna(top["avg_rating"]) else round(top["hist_avg_min"], 1),
            "ratings": int(top["total_ratings"]),
            "status": "✅ at target" if need == (0, 0) else f"⚠ needs {need[0]}" + (f"–{need[1]}" if need[1] != need[0] else "") + " 5★",
            "bought/month": top.get("bought_text") or "", "sub-rank": _num_or_none(top.get("bsr_sub")),
            "rank category": top.get("bsr_sub_cat") or "", "price": _num_or_none(top.get("price")),
            "in stock": top.get("availability") or "",
            "reviews 14d": int(cur["n"]) if cur is not None else 0,
            "★ 14d": round(cur["avg_stars"], 2) if cur is not None and cur["n"] else None,
            "★ prev 14d": round(prev["avg_stars"], 2) if prev is not None and prev["n"] and prev["complete"] else None,
            "% 1–2★ 14d": cur["neg_share"] if cur is not None and cur["n"] else None,
            "top complaint 30d": top_issue,
            "safety 30d": int(safety.get((plat, unit), pd.DataFrame()).shape[0]),
        })
    return pd.DataFrame(rows).sort_values(["platform", "ratings"], ascending=[True, False]).reset_index(drop=True)


def _num_or_none(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


def _safety(inp: Inputs, today: date, days: int) -> dict[tuple[str, str], pd.DataFrame]:
    if inp.labels.empty or inp.reviews.empty:
        return {}
    latest = latest_snapshots(inp.snaps)
    names = pool_labels(latest) if not latest.empty else {}
    r = inp.reviews.copy()
    r["platform"] = r["platform"].fillna("amazon")
    r["pool"] = r["parent_asin"].fillna(r["asin"])
    r["unit"] = r["product"].where(r["product"].notna(), r["pool"].map(names)).fillna(r["pool"])
    since = (today - timedelta(days=days - 1)).isoformat()
    s = r.merge(inp.labels[inp.labels["severity"] == "safety"][["review_id", "issues"]], on="review_id")
    s = s[s["review_date"].fillna("") >= since]
    return {k: g for k, g in s.groupby(["platform", "unit"])}


# ---------------------------------------------------------------- insights

def build_insights(inp: Inputs, today: date) -> list[Insight]:
    out: list[Insight] = []
    latest = latest_snapshots(inp.snaps)
    names = pool_labels(latest) if not latest.empty else {}
    unb = unbiased_reviews(inp.reviews, names)

    # 1. safety complaints in the last 30 days
    for (plat, unit), g in _safety(inp, today, 30).items():
        quotes = [f"{i['evidence']}" for js in g["issues"] for i in json.loads(js or "[]") if i.get("code") == "heat_safety"]
        ev = g[["review_date", "rating", "title", "body"]].sort_values("review_date", ascending=False)
        out.append(Insight("act", f"Safety complaints · {unit} ({plat.title()})",
                           f"{len(g)} review(s) in the last 30 days mention burning, overheating or injury"
                           + (f": “{quotes[0][:90]}”" if quotes else "") + ".",
                           "Review issues (AI) → filter heat_safety; Alerts", ev, f"safety:{plat}:{unit}",
                           str(len(g)), "reviews · 30 days"))

    # 1b. safety pattern over 90 days (products not already flagged for the last 30)
    recent = {(i.key.split(":", 2)[1], i.key.split(":", 2)[2]) for i in out if i.key.startswith("safety:")}
    older = {k: g for k, g in _safety(inp, today, 90).items() if k not in recent}
    if older:
        ev = pd.concat([g.assign(product=k[1], platform=k[0]) for k, g in older.items()])[
            ["review_date", "platform", "product", "rating", "title", "body"]].sort_values("review_date", ascending=False)
        out.append(Insight("watch", f"Safety complaints in the last 90 days · {len(older)} product(s)",
                           "; ".join(f"{k[1]} ({k[0].title()}): {len(g)}" for k, g in older.items())
                           + ". None in the last 30 days.", "Review issues (AI) → heat_safety", ev, "safety90",
                           str(sum(len(g) for g in older.values())), "reviews · 90 days"))

    # 2. stock-outs on Amazon
    if not latest.empty and "availability" in latest:
        oos = latest[latest["availability"].fillna("In stock").str.contains("unavailable|out of stock", case=False)]
        if len(oos):
            out.append(Insight("act", f"{len(oos)} Amazon listing(s) not in stock",
                               ", ".join(oos["product"].astype(str)) + ".", "Ratings & reviews → Amazon",
                               oos[["asin", "product", "availability", "captured_at"]], "stock", str(len(oos)), "listings"))

    # 3. below the rating target
    card = scorecard(inp, today)
    if not card.empty:
        below = card[card["status"].str.startswith("⚠")]
        for plat, g in below.groupby("platform"):
            total = int((card["platform"] == plat).sum())
            worst = g.sort_values("ratings", ascending=False).head(3)
            names_txt = "; ".join(f"{r.product} {r.rating}★ ({r.status.replace('⚠ ', '')})" for r in worst.itertuples())
            sev = "act" if (g["rating"] < inp.target_shown - 0.3).any() else "watch"
            out.append(Insight(sev, f"{len(g)} of {total} {plat.title()} products are below {inp.target_shown}★",
                               f"Largest by ratings: {names_txt}.",
                               f"Ratings & reviews → {plat.title()} (4.1 calculator)",
                               g[["product", "rating", "ratings", "status", "reviews 14d", "★ 14d"]], f"below:{plat}",
                               f"{len(g)}/{total}", f"below {inp.target_shown}★"))

    # 4. worsening / improving review trends (latest complete window vs the one before)
    trends = review_trends(unb, today, 3)
    for (plat, unit), g in trends.groupby(["platform", "unit"]) if not trends.empty else []:
        g = g.sort_values("window")
        cur, prev = g.iloc[0], g.iloc[1]
        if not (cur["complete"] and prev["complete"]) or cur["n"] < MIN_N or prev["n"] < MIN_N:
            continue
        d_star = cur["avg_stars"] - prev["avg_stars"]
        d_neg = cur["neg_share"] - prev["neg_share"]
        detail = (f"{cur['label']}: {cur['avg_stars']:.2f}★, {cur['neg_share']:.0%} 1–2★ (n={cur['n']}) vs "
                  f"{prev['label']}: {prev['avg_stars']:.2f}★, {prev['neg_share']:.0%} (n={prev['n']}).")
        ev = unb[(unb["platform"] == plat) & (unb["unit"] == unit) & (unb["day"] >= prev["start"])][
            ["review_date", "rating", "title", "body"]].sort_values("review_date", ascending=False)
        if d_star <= -STAR_DROP or d_neg >= NEG_RISE:
            out.append(Insight("watch", f"Reviews getting worse · {unit} ({plat.title()})", detail,
                               f"Overview → Bi-weekly trends → {unit}", ev, f"worse:{plat}:{unit}",
                               f"{d_star:+.2f}★", "vs previous 14 days"))
        elif d_star >= STAR_DROP or d_neg <= -NEG_RISE:
            out.append(Insight("good", f"Reviews improving · {unit} ({plat.title()})", detail,
                               f"Overview → Bi-weekly trends → {unit}", ev, f"better:{plat}:{unit}",
                               f"{d_star:+.2f}★", "vs previous 14 days"))

    # 5. issue spikes among <=3 star reviews: last 28 days vs the 28 before
    cur_mix = issue_mix(unb, inp.labels, today - timedelta(days=27), today)
    prev_mix = issue_mix(unb, inp.labels, today - timedelta(days=55), today - timedelta(days=28))
    for (plat, unit), g in cur_mix.groupby(["platform", "unit"]) if not cur_mix.empty else []:
        if g["coverage"].iloc[0] < MIN_LABEL_COVERAGE or g["labelled"].iloc[0] < MIN_N:
            continue
        p = prev_mix[(prev_mix["platform"] == plat) & (prev_mix["unit"] == unit)]
        if p.empty or p["coverage"].iloc[0] < MIN_LABEL_COVERAGE or p["labelled"].iloc[0] < MIN_N:
            continue
        for r in g.itertuples():
            share = r.reviews / r.labelled
            pr = p[p["code"] == r.code]
            pshare = (pr["reviews"].iloc[0] / pr["labelled"].iloc[0]) if len(pr) else 0.0
            if share - pshare >= 0.15 and r.reviews >= 4:
                out.append(Insight("watch", f"Rising complaint · {r.code} on {unit} ({plat.title()})",
                                   f"{share:.0%} of labelled ≤3★ reviews in the last 28 days ({r.reviews}/{r.labelled}) "
                                   f"vs {pshare:.0%} in the 28 days before.",
                                   f"Review issues (AI) → {unit} · {r.code}", None, f"issue:{plat}:{unit}:{r.code}",
                                   f"{share:.0%}", "of unhappy reviews"))

    # 6. product-page signals once 14 days of history exist
    ch = snapshot_change(inp.snaps, today)
    if not ch.empty and ch["has_history"].any():
        for r in ch[ch["has_history"]].itertuples():
            prod = latest.set_index("asin").loc[r.asin, "product"] if r.asin in set(latest["asin"]) else r.asin
            if pd.notna(r.bought_then) and pd.notna(r.bought_now) and r.bought_now < r.bought_then:
                out.append(Insight("watch", f"Sales signal down · {prod}",
                                   f"Amazon 'bought in past month' fell from {int(r.bought_then):,}+ to {int(r.bought_now):,}+.",
                                   "Overview → Product scorecard", None, f"bought:{r.asin}"))
            if pd.notna(r.bsr_sub_then) and pd.notna(r.bsr_sub_now) and r.bsr_sub_now > 1.5 * r.bsr_sub_then + 2:
                out.append(Insight("watch", f"Category rank slipping · {prod}",
                                   f"Sub-category rank #{int(r.bsr_sub_then)} → #{int(r.bsr_sub_now)} over 14 days.",
                                   "Overview → Product scorecard", None, f"rank:{r.asin}"))
    elif not ch.empty:
        out.append(Insight("info", "Rating, rank and sales-signal trends are collecting",
                           f"Snapshots since {ch['since'].min():%d %b}; 14-day comparisons start "
                           f"{ch['since'].min() + timedelta(days=WINDOW):%d %b}.", "Ratings & reviews", None, "collecting"))

    # 7. returns (sheet data: last month vs the 3 before)
    if not inp.approved.empty:
        a = inp.approved.dropna(subset=["product"])
        months = sorted(a["month"].unique())
        if len(months) >= 4:
            last, base = months[-1], months[-4:-1]
            per = a.groupby(["product", "month"]).size().unstack(fill_value=0)
            spikes = []
            for prod, row in per.iterrows():
                avg = row[base].mean()
                if row[last] >= 10 and row[last] >= 1.5 * max(avg, 1):
                    spikes.append({"product": prod, f"returns {last}": int(row[last]),
                                   f"avg/month {base[0]}–{base[-1]}": round(avg, 1),
                                   "× usual": round(row[last] / max(avg, 1), 1)})
            if spikes:
                ev = pd.DataFrame(spikes).sort_values(f"returns {last}", ascending=False)
                worst = "; ".join(f"{r['product']} {r[f'returns {last}']} vs {r[f'avg/month {base[0]}–{base[-1]}']:.0f}/mo"
                                  for r in ev.head(3).to_dict("records"))
                out.append(Insight("watch", f"Return spikes in {last} · {len(ev)} product(s)",
                                   f"Approved returns + exchanges at 1.5× or more their usual monthly level: {worst}. "
                                   f"(Returns data runs through {last}.)",
                                   "Returns & exchanges → filter product and month", ev, "returns",
                                   str(len(ev)), f"products · {last}"))

    # 8. data health
    if inp.last_sync_at:
        age = (pd.Timestamp(today) - pd.Timestamp(inp.last_sync_at).normalize()).days
        if age > 2:
            out.append(Insight("info", "Returns data isn't updating",
                               f"The last successful sheet sync was {inp.last_sync_at[:10]} ({age} days ago). "
                               "Connect the Google Sheet (SETUP.md §4) so returns stay current.",
                               "Sidebar → Pipeline health", None, "sync"))
    if not inp.reviews.empty:
        labelled = set(inp.labels["review_id"]) if not inp.labels.empty else set()
        backlog = int((~inp.reviews["review_id"].isin(labelled)).sum())
        if backlog:
            out.append(Insight("info", f"{backlog} reviews not yet AI-labelled",
                               "Issue numbers cover labelled reviews only."
                               + ("" if inp.has_ai_key else " No AI key is configured here, so new reviews stay unlabelled."),
                               "Review issues (AI)", None, "backlog"))
    if not inp.labels.empty:
        waiting = int((inp.labels["status"].eq("queue") & inp.labels["human_verdict"].isna()).sum())
        if waiting:
            out.append(Insight("info", f"{waiting} AI labels waiting for a person",
                               "Checking them turns on the accuracy score for issue shares.",
                               "Review issues (AI) → Review queue", None, "queue"))

    # 9. healthy products
    if not card.empty:
        ok = card[card["status"].str.startswith("✅")]
        worse = {i.key.split(":", 2)[2] for i in out if i.key.startswith("worse:")}
        ok = ok[~ok["product"].isin(worse)]
        if len(ok):
            out.append(Insight("good", f"{len(ok)} product listings at or above {inp.target_shown}★ with no worsening trend",
                               ", ".join(f"{r.product} ({r.platform.title()} {r.rating}★)" for r in ok.head(8).itertuples())
                               + ("…" if len(ok) > 8 else ""), "Overview → Product scorecard",
                               ok[["platform", "product", "rating", "ratings"]], "healthy", str(len(ok)), "listings on target"))

    return sorted(out, key=lambda i: SEVERITY_ORDER.get(i.severity, 9))


# ---------------------------------------------------------------- digest

DIGEST_ANCHOR = date(2026, 1, 5)   # a Monday; digests cover fixed 14-day periods from here


def digest_period(today: date) -> date:
    return today - timedelta(days=(today - DIGEST_ANCHOR).days % WINDOW)


def digest_text(insights: list[Insight], limit: int = 8) -> str:
    icon = {"act": "🔴", "watch": "🟠", "info": "🔵", "good": "🟢"}
    lines = [f"{icon.get(i.severity, '•')} {i.title} — {i.detail}" for i in insights[:limit]]
    counts = {s: sum(1 for i in insights if i.severity == s) for s in ("act", "watch", "good")}
    head = f"{counts['act']} to act on · {counts['watch']} to watch · {counts['good']} healthy"
    return head + "\n" + "\n".join(lines)


def save_digest(con, today: date, insights: list[Insight]) -> tuple[bool, str, str]:
    """Stores one digest per 14-day period. Returns (created, period_start, text)."""
    con.execute("CREATE TABLE IF NOT EXISTS digest (period_start TEXT PRIMARY KEY, created_at TEXT, body TEXT, items TEXT)")
    period = digest_period(today).isoformat()
    text = digest_text(insights)
    cur = con.execute("INSERT OR IGNORE INTO digest VALUES (?,?,?,?)",
                      (period, pd.Timestamp.now().isoformat(timespec="seconds"), text,
                       json.dumps([i.as_dict() for i in insights])))
    return bool(cur.rowcount), period, text
