"""Visual layer for the dashboard: one stylesheet and small HTML helpers."""

from __future__ import annotations

import html

SEVERITY = {
    "act": {"label": "Act now", "color": "#DC2626", "tint": "#FEF2F2", "dot": "●"},
    "watch": {"label": "Watch", "color": "#D97706", "tint": "#FFFBEB", "dot": "●"},
    "good": {"label": "Doing well", "color": "#059669", "tint": "#ECFDF5", "dot": "●"},
    "info": {"label": "Notes", "color": "#2563EB", "tint": "#EFF6FF", "dot": "●"},
}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"], .stMarkdown, .stText, button, input, textarea { font-family: 'Inter', system-ui, sans-serif; }
[data-testid="stAppViewContainer"] > .main .block-container, .block-container { padding-top: 1.6rem; max-width: 1320px; }
#MainMenu, footer, [data-testid="stDecoration"] { display: none !important; }
h1, h2, h3 { letter-spacing: -0.02em; }

/* header */
.cph-header { display:flex; align-items:center; justify-content:space-between; gap:16px; margin: 0 0 6px 0; }
.cph-title { font-size: 1.65rem; font-weight: 800; color:#111827; margin:0; }
.cph-sub { color:#6B7280; font-size:.9rem; margin-top:2px; }
.cph-pill { background:#EEF2FF; color:#4338CA; border-radius:999px; padding:6px 12px; font-size:.8rem; font-weight:600; white-space:nowrap; }

/* tabs */
.stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid #E5E7EB; }
.stTabs [data-baseweb="tab"] { padding: 8px 14px; border-radius: 8px 8px 0 0; font-weight: 500; color:#4B5563; }
.stTabs [aria-selected="true"] { color:#4F46E5 !important; background:#FFFFFF; }

/* section titles */
.cph-section { display:flex; align-items:baseline; gap:10px; margin: 28px 0 10px 0; }
.cph-section .h { font-size:1.3rem; font-weight:800; margin:0; color:#111827; letter-spacing:-0.02em; }
.cph-section span { color:#6B7280; font-size:.85rem; }

/* KPI tiles */
.cph-tiles { display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:12px; margin: 6px 0 4px 0; }
.cph-tile { background:#FFFFFF; border:1px solid #E5E7EB; border-radius:14px; padding:14px 16px; box-shadow: 0 1px 2px rgba(16,24,40,.04); }
.cph-tile .lbl { font-size:.78rem; font-weight:600; text-transform:uppercase; letter-spacing:.04em; display:flex; align-items:center; gap:6px; }
.cph-tile .val { font-size:1.9rem; font-weight:800; color:#111827; line-height:1.1; margin-top:4px; }
.cph-tile .hint { font-size:.78rem; color:#6B7280; margin-top:2px; }

/* glance strip */
.cph-glance { display:grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap:12px; }
.cph-glance div.g { background:#FFFFFF; border:1px solid #E5E7EB; border-radius:12px; padding:12px 14px; }
.cph-glance .k { color:#6B7280; font-size:.75rem; font-weight:600; text-transform:uppercase; letter-spacing:.04em; }
.cph-glance .v { font-size:1.35rem; font-weight:700; color:#111827; margin-top:2px; }
.cph-glance .s { font-size:.75rem; color:#6B7280; }

/* insight cards: the Streamlit container keyed card_<sev>_<n> is styled as the card */
[class*="st-key-card_"] { background:#FFFFFF; border:1px solid #E5E7EB; border-radius:14px; padding:14px 16px 6px 16px;
                          box-shadow: 0 1px 2px rgba(16,24,40,.04); gap: 6px; }
[class*="st-key-card_act"] { border-left: 5px solid #DC2626; }
[class*="st-key-card_watch"] { border-left: 5px solid #D97706; }
[class*="st-key-card_good"] { border-left: 5px solid #059669; }
[class*="st-key-card_info"] { border-left: 5px solid #2563EB; }
.cph-card { display:flex; gap:14px; align-items:flex-start; }
.cph-metric { width: 92px; flex: 0 0 92px; text-align:center; border-radius:12px; padding:8px 6px; }
.cph-metric .n { font-size:1.3rem; font-weight:800; line-height:1.1; white-space:nowrap; }
.cph-metric .u { font-size:.66rem; font-weight:600; line-height:1.2; margin-top:2px; }
.cph-body .t { font-weight:700; color:#111827; font-size:.98rem; line-height:1.35; }
.cph-body .d { color:#374151; font-size:.88rem; line-height:1.45; margin-top:4px; }
.cph-where { display:inline-block; margin-top:8px; background:#F3F4F6; color:#4B5563; border-radius:999px; padding:3px 10px; font-size:.75rem; }
[class*="st-key-card_"] [data-testid="stExpander"] details { border: none; background: transparent; }
[class*="st-key-card_"] [data-testid="stExpander"] summary { padding: 4px 0; font-size:.82rem; color:#4F46E5; }
[class*="st-key-card_"] [data-testid="stExpander"] summary:hover { color:#3730A3; }

/* compact insight board */
.ib-head { display:flex; align-items:center; gap:8px; margin: 2px 0 8px 0; }
.ib-head .t { font-weight:800; font-size:.95rem; color:#111827; }
.ib-head .c { border-radius:999px; padding:1px 9px; font-size:.75rem; font-weight:800; }
.ib-empty { color:#9CA3AF; font-size:.85rem; padding:10px 2px; }
[class*="st-key-ib_"] { background:#FFFFFF; border:1px solid #E5E7EB; border-radius:12px; padding:10px 12px 4px 12px; gap:6px; }
[class*="st-key-ib_"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
[class*="st-key-ib_"] [data-testid="stMarkdownContainer"] p { margin-bottom: 0; }
[class*="st-key-ib_act"] { border-left:4px solid #DC2626; }
[class*="st-key-ib_watch"] { border-left:4px solid #D97706; }
[class*="st-key-ib_good"] { border-left:4px solid #059669; }
.ib { display:flex; gap:10px; align-items:flex-start; }
.ib .m { flex:0 0 auto; min-width:52px; text-align:center; border-radius:8px; padding:3px 6px; font-weight:800; font-size:.9rem; line-height:1.3; }
.ib .x { min-width:0; }
.ib .tt { font-weight:700; font-size:.88rem; color:#111827; line-height:1.3; }
.ib .ss { font-size:.8rem; color:#4B5563; line-height:1.35; margin-top:2px; }
[class*="st-key-ib_"] [data-testid="stExpander"] details { border:none; background:transparent; }
[class*="st-key-ib_"] [data-testid="stExpander"] { margin-top: 0; }
[class*="st-key-ib_"] [data-testid="stExpander"] summary { padding:0 0 2px 62px; font-size:.76rem; color:#6366F1; min-height:0; }
.ib-detail { font-size:.82rem; line-height:1.45; color:#374151; }
.ib-detail .w { color:#6B7280; font-size:.76rem; margin-top:4px; }
[class*="st-key-ib_"] [data-testid="stExpanderDetails"] { padding: 4px 4px 8px 4px; }
.cph-statusline { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.cph-statusline span { border-radius:999px; padding:3px 10px; font-size:.78rem; font-weight:700; }

/* generic panels */
[class*="st-key-panel_"] { background:#FFFFFF; border:1px solid #E5E7EB; border-radius:14px; padding:14px 16px; }
.cph-quote { background:#F9FAFB; border-left:3px solid #A5B4FC; padding:10px 14px; border-radius:8px; color:#374151; font-size:.9rem; }
.cph-chips { margin-top:8px; display:flex; flex-wrap:wrap; gap:6px; }
.cph-chips span { background:#EEF2FF; color:#3730A3; border-radius:999px; padding:3px 10px; font-size:.75rem; font-weight:500; }
[data-testid="stSidebar"] { background:#FFFFFF; border-right:1px solid #E5E7EB; }

/* product tiles */
[class*="st-key-tile_"] { background:#FFFFFF; border:1px solid #E5E7EB; border-radius:16px; padding:12px 12px 8px 12px;
                          box-shadow:0 1px 2px rgba(16,24,40,.04); transition: box-shadow .15s ease, transform .15s ease; gap:4px; }
[class*="st-key-tile_"]:hover { box-shadow:0 8px 24px rgba(16,24,40,.10); transform: translateY(-2px); }
[class*="st-key-tile_"] [data-testid="stButton"] { margin-top:auto; }
[class*="st-key-tile_"] button { width:100%; border-radius:10px; font-size:.82rem; padding:4px 0; min-height:0;
                                 background:#EEF2FF; color:#3730A3; border:1px solid #E0E7FF; font-weight:600; }
[class*="st-key-tile_"] button:hover { background:#E0E7FF; color:#312E81; border-color:#C7D2FE; }
.pt-img { height:150px; flex-shrink:0; border-radius:12px; background:#F9FAFB; display:flex; align-items:center; justify-content:center; overflow:hidden; position:relative; }
.pt-img img { max-height:140px; max-width:100%; object-fit:contain; mix-blend-mode:multiply; }
.pt-ph { font-size:2rem; font-weight:800; color:#C7D2FE; }
.pt-score { position:absolute; top:8px; right:8px; border-radius:999px; padding:3px 9px; font-size:.78rem; font-weight:800; color:#fff; }
.pt-name { font-weight:700; color:#111827; font-size:.92rem; margin-top:8px; line-height:1.25; min-height:2.3em; }
.pt-row { display:flex; align-items:baseline; justify-content:space-between; gap:6px; margin-top:4px; }
.pt-rating { font-size:1.35rem; font-weight:800; color:#111827; }
.pt-delta { font-size:.8rem; font-weight:700; border-radius:6px; padding:1px 6px; }
.pt-meta { font-size:.74rem; color:#6B7280; margin-top:2px; }
.pt-chips { display:flex; flex-wrap:wrap; gap:4px; margin-top:6px; }
.pt-chips span { background:#F3F4F6; color:#374151; border-radius:999px; padding:2px 8px; font-size:.7rem; }

/* ticker strip */
.tk { display:flex; gap:10px; overflow-x:auto; padding:2px 2px 8px 2px; }
.tk .i { flex:0 0 auto; background:#FFFFFF; border:1px solid #E5E7EB; border-radius:12px; padding:8px 12px; display:flex; gap:10px; align-items:center; }
.tk .n { font-weight:700; font-size:.85rem; color:#111827; white-space:nowrap; }
.tk .v { font-weight:800; font-size:.85rem; }

/* product detail */
.pd-hero { display:flex; gap:20px; align-items:center; }
.pd-hero .img { width:180px; height:180px; border-radius:16px; background:#F9FAFB; display:flex; align-items:center; justify-content:center; }
.pd-hero .img img { max-width:170px; max-height:170px; object-fit:contain; mix-blend-mode:multiply; }
.pd-name { font-size:1.6rem; font-weight:800; color:#111827; letter-spacing:-0.02em; }
.pd-gauge { width:120px; height:120px; }
.sig { margin:6px 0; }
.sig .top { display:flex; justify-content:space-between; font-size:.82rem; color:#374151; }
.sig .bar { height:8px; background:#F3F4F6; border-radius:999px; overflow:hidden; margin-top:3px; }
.sig .bar div { height:100%; border-radius:999px; }
.cph-status { display:flex; align-items:center; gap:8px; font-weight:600; font-size:.9rem; margin: 4px 0 2px 0; }
.cph-status .b { border-radius:999px; padding:3px 10px; font-size:.75rem; font-weight:700; }
</style>
"""


def e(s) -> str:
    return html.escape(str(s))


def header(title: str, sub: str, pill: str) -> str:
    return (f'<div class="cph-header"><div><div class="cph-title">{e(title)}</div>'
            f'<div class="cph-sub">{e(sub)}</div></div><div class="cph-pill">{e(pill)}</div></div>')


def status_badge(status: str, when: str, published: bool) -> str:
    colors = {"pass": ("#ECFDF5", "#047857"), "warn": ("#FFFBEB", "#B45309"), "fail": ("#FEF2F2", "#B91C1C")}
    bg, fg = colors.get(status, ("#F3F4F6", "#374151"))
    note = "published" if published else "blocked — showing previous data"
    return (f'<div class="cph-status"><span class="b" style="background:{bg};color:{fg}">{e(status.upper())}</span>'
            f'<span style="color:#6B7280;font-weight:500;font-size:.8rem">{e(when)} · {e(note)}</span></div>')


def section(title: str, hint: str = "") -> str:
    return f'<div class="cph-section"><div class="h">{e(title)}</div><span>{e(hint)}</span></div>'


def tiles(counts: dict[str, int], hints: dict[str, str]) -> str:
    cells = []
    for sev in ("act", "watch", "good", "info"):
        c = SEVERITY[sev]
        cells.append(f'<div class="cph-tile" style="background:{c["tint"]};border-color:{c["tint"]}">'
                     f'<div class="lbl" style="color:{c["color"]}">{c["dot"]} {c["label"]}</div>'
                     f'<div class="val">{counts.get(sev, 0)}</div><div class="hint">{e(hints.get(sev, ""))}</div></div>')
    return f'<div class="cph-tiles">{"".join(cells)}</div>'


def glance(items: list[tuple[str, str, str]]) -> str:
    return '<div class="cph-glance">' + "".join(
        f'<div class="g"><div class="k">{e(k)}</div><div class="v">{e(v)}</div><div class="s">{e(s)}</div></div>'
        for k, v, s in items) + "</div>"


def card(ins) -> str:
    c = SEVERITY.get(ins.severity, SEVERITY["info"])
    metric = ""
    if getattr(ins, "metric", ""):
        metric = (f'<div class="cph-metric" style="background:{c["tint"]};color:{c["color"]}">'
                  f'<div class="n">{e(ins.metric)}</div><div class="u">{e(ins.metric_label)}</div></div>')
    title = ins.title.split(" · ")
    t = e(title[0]) + (f' <span style="color:#6B7280;font-weight:500">· {e(" · ".join(title[1:]))}</span>' if len(title) > 1 else "")
    return (f'<div class="cph-card">{metric}<div class="cph-body"><div class="t">{t}</div>'
            f'<div class="d">{e(ins.detail)}</div><div class="cph-where">↗ {e(ins.where)}</div></div></div>')


def quote(text: str, chips: list[str] | None = None) -> str:
    chip_html = "".join(f"<span>{e(c)}</span>" for c in (chips or []))
    return f'<div class="cph-quote">{e(text)}</div>' + (f'<div class="cph-chips">{chip_html}</div>' if chip_html else "")


GRADE_COLOR = {"Healthy": "#059669", "Watch": "#D97706", "At risk": "#DC2626", "n/a": "#9CA3AF"}


def score_color(score) -> str:
    if score is None:
        return GRADE_COLOR["n/a"]
    return GRADE_COLOR["Healthy"] if score >= 75 else GRADE_COLOR["Watch"] if score >= 55 else GRADE_COLOR["At risk"]


def sparkline(values: list[float], width: int = 220, height: int = 40, lo: float = 1.0, hi: float = 5.0) -> str:
    """Inline SVG line of bi-weekly average stars (scale 1-5), with a 4.1 reference line."""
    if len(values) < 2:
        return f'<svg width="100%" height="{height}" viewBox="0 0 {width} {height}"></svg>'
    def y(v):
        return height - 4 - (max(lo, min(hi, v)) - lo) / (hi - lo) * (height - 8)
    step = (width - 8) / (len(values) - 1)
    pts = " ".join(f"{4 + i * step:.1f},{y(v):.1f}" for i, v in enumerate(values))
    color = "#059669" if values[-1] >= values[0] else "#DC2626"
    area = f"4,{height} " + pts + f" {4 + (len(values) - 1) * step:.1f},{height}"
    return (f'<svg width="100%" height="{height}" viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
            f'<polygon points="{area}" fill="{color}" opacity="0.08"/>'
            f'<line x1="0" x2="{width}" y1="{y(4.1):.1f}" y2="{y(4.1):.1f}" stroke="#9CA3AF" stroke-dasharray="3,3" stroke-width="1"/>'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.2" stroke-linejoin="round"/>'
            f'<circle cx="{4 + (len(values) - 1) * step:.1f}" cy="{y(values[-1]):.1f}" r="3" fill="{color}"/></svg>')


def _img(url, name, cls="pt-img"):
    initials = "".join(w[0] for w in str(name).replace("Cult ", "").split()[:2]).upper()
    inner = f'<img src="{e(url)}" alt="{e(name)}" loading="lazy" referrerpolicy="no-referrer">' if url \
        else f'<div class="pt-ph">{e(initials)}</div>'
    return inner


def product_tile(c) -> str:
    sc = "–" if c.score is None else f"{int(c.score)}"
    delta = ""
    if c.delta_stars is not None:
        up = c.delta_stars >= 0
        delta = (f'<span class="pt-delta" style="background:{"#ECFDF5" if up else "#FEF2F2"};'
                 f'color:{"#047857" if up else "#B91C1C"}">{"▲" if up else "▼"} {abs(c.delta_stars):.2f}★</span>')
    chips = "".join(f"<span>{e(pl.title())} {r}★ · {n:,}</span>" for pl, (r, n) in sorted(c.platform_ratings.items()))
    meta = []
    if c.new_reviews_14d:
        meta.append(f"{c.new_reviews_14d} new reviews/14d")
    if c.returns_ratio is not None:
        meta.append(f"returns {c.returns_ratio:.1f}× usual")
    if c.safety_90d:
        meta.append(f"⚠ {c.safety_90d} safety")
    rating = f"{c.rating}★" if c.rating is not None else "—"
    return (f'<div style="padding-bottom:10px"><div class="pt-img">{_img(c.image_url, c.product)}'
            f'<div class="pt-score" style="background:{score_color(c.score)}">{sc}</div></div>'
            f'<div class="pt-name">{e(c.product)}</div>'
            f'<div class="pt-row"><span class="pt-rating">{rating}</span>{delta}</div>'
            f'{sparkline([v for _, v, _ in c.series])}'
            f'<div class="pt-meta">{e(" · ".join(meta)) or "&nbsp;"}</div>'
            f'<div class="pt-chips">{chips}</div></div>')


def ticker(items: list[tuple[str, float]]) -> str:
    cells = []
    for name, d in items:
        up = d >= 0
        cells.append(f'<div class="i"><span class="n">{e(name)}</span><span class="v" style="color:'
                     f'{"#059669" if up else "#DC2626"}">{"▲" if up else "▼"} {abs(d):.2f}★</span></div>')
    return f'<div class="tk">{"".join(cells)}</div>'


def gauge(score) -> str:
    color = score_color(score)
    pct = 0 if score is None else max(0, min(100, score))
    r, circ = 48, 2 * 3.14159 * 48
    return (f'<svg class="pd-gauge" viewBox="0 0 120 120"><circle cx="60" cy="60" r="{r}" fill="none" stroke="#F3F4F6" stroke-width="12"/>'
            f'<circle cx="60" cy="60" r="{r}" fill="none" stroke="{color}" stroke-width="12" stroke-linecap="round" '
            f'stroke-dasharray="{circ * pct / 100:.1f} {circ:.1f}" transform="rotate(-90 60 60)"/>'
            f'<text x="60" y="66" text-anchor="middle" font-size="28" font-weight="800" fill="#111827">'
            f'{"–" if score is None else int(score)}</text></svg>')


def product_hero(c, links_html: str) -> str:
    chips = "".join(f"<span>{e(pl.title())} {r}★ · {n:,} ratings</span>" for pl, (r, n) in sorted(c.platform_ratings.items()))
    return (f'<div class="pd-hero"><div class="img">{_img(c.image_url, c.product)}</div>'
            f'<div style="flex:1"><div class="pd-name">{e(c.product)}</div>'
            f'<div style="color:#6B7280;margin-top:2px">{e(c.category.title())} · health '
            f'<b style="color:{score_color(c.score)}">{e(c.grade)}</b> · based on {len(c.signals)} of 5 signals</div>'
            f'<div class="pt-chips" style="margin-top:10px">{chips}</div>'
            f'<div style="margin-top:10px;font-size:.85rem">{links_html}</div></div>'
            f'<div>{gauge(c.score)}</div></div>')


def signal_bars(signals: dict, labels: dict, weights: dict) -> str:
    rows = []
    for k in ("rating", "recent", "negative", "safety", "returns"):
        if k in signals:
            v, why = signals[k]
            rows.append(f'<div class="sig"><div class="top"><span><b>{e(labels[k])}</b> · {e(why)}</span>'
                        f'<span>{int(v)} · weight {int(weights[k] * 100)}%</span></div>'
                        f'<div class="bar"><div style="width:{v}%;background:{score_color(v)}"></div></div></div>')
        else:
            rows.append(f'<div class="sig"><div class="top" style="color:#9CA3AF"><span>{e(labels[k])}</span>'
                        f'<span>not enough data</span></div></div>')
    return "".join(rows)


def board_head(sev: str, count: int) -> str:
    c = SEVERITY[sev]
    return (f'<div class="ib-head"><span class="t">{e(c["label"])}</span>'
            f'<span class="c" style="background:{c["tint"]};color:{c["color"]}">{count}</span></div>')


def board_item(ins) -> str:
    c = SEVERITY.get(ins.severity, SEVERITY["info"])
    metric = (f'<div class="m" style="background:{c["tint"]};color:{c["color"]}">{e(ins.metric)}</div>'
              if getattr(ins, "metric", "") else "")
    short = getattr(ins, "short", "") or ins.detail
    if len(short) > 120:
        short = short[:117].rstrip() + "…"
    return f'<div class="ib">{metric}<div class="x"><div class="tt">{e(ins.title)}</div><div class="ss">{e(short)}</div></div></div>'


def status_line(counts: dict) -> str:
    parts = []
    for sev in ("act", "watch", "good"):
        c = SEVERITY[sev]
        parts.append(f'<span style="background:{c["tint"]};color:{c["color"]}">{counts.get(sev, 0)} {e(c["label"].lower())}</span>')
    return f'<div class="cph-statusline">{"".join(parts)}</div>'
