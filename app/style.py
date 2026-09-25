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

/* generic panels */
[class*="st-key-panel_"] { background:#FFFFFF; border:1px solid #E5E7EB; border-radius:14px; padding:14px 16px; }
.cph-quote { background:#F9FAFB; border-left:3px solid #A5B4FC; padding:10px 14px; border-radius:8px; color:#374151; font-size:.9rem; }
.cph-chips { margin-top:8px; display:flex; flex-wrap:wrap; gap:6px; }
.cph-chips span { background:#EEF2FF; color:#3730A3; border-radius:999px; padding:3px 10px; font-size:.75rem; font-weight:500; }
[data-testid="stSidebar"] { background:#FFFFFF; border-right:1px solid #E5E7EB; }
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
