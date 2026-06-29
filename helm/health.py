# helm/health.py
# HELM Health Map — CSP position scoring + HTML rendering.
# Spec: HEALTH_MAP.md (v9). Ported to helm-server GET /health.
#
# Public API:
#   gather_csp(conn, ticker=None) -> list[dict]   raw rows + derived metrics
#   score_position(row)           -> dict          per-variable scores + composite
#   render(conn, ticker=None)     -> str           full standalone HTML page
#
# Greeks (delta / theta / delta_vs_entry) are frequently NULL right now
# (helm check --silent not capturing greeks). Those cells render gray and
# drop out of the composite, which is renormalised over the variables that
# do have data. delta x DTE uses the spec's neutral default of 6 when delta
# is missing so it stays weighted.

from __future__ import annotations
from helm.models.check import _pnl_pick

# ── Cell colour stops (per individual 0-10 score) ────────────────────────────
# Each: bg, border, label-text, value-text
CELL = {
    "green":  {"bg": "#eaf3de", "bd": "#97c459", "tx": "#27500a", "vl": "#3b6d11"},
    "lgreen": {"bg": "#f2f8e6", "bd": "#b8d97a", "tx": "#3b6d11", "vl": "#639922"},
    "amber":  {"bg": "#faeeda", "bd": "#fac775", "tx": "#633806", "vl": "#ba7517"},
    "orange": {"bg": "#fdf0da", "bd": "#f0b050", "tx": "#854f0b", "vl": "#d85a30"},
    "red":    {"bg": "#fcebeb", "bd": "#f09595", "tx": "#791f1f", "vl": "#a32d2d"},
    "gray":   {"bg": "#f5f5f3", "bd": "#dddcd5", "tx": "#888780", "vl": "#5f5e5a"},
}


def cell_color(score):
    if score is None:
        return "gray"
    if score >= 7:
        return "green"
    if score >= 3:
        return "amber"
    return "red"


def composite_band(c):
    """Composite 0-100 -> (band_name, colour_key)."""
    if c is None:
        return ("no data", "gray")
    if c >= 70:
        return ("healthy", "green")
    if c >= 40:
        return ("watch", "amber")
    return ("at risk", "red")


# ── Per-variable scorers (return 0-10, or None for "no data / gray") ─────────
def s_be_buffer(buf):
    if buf is None:
        return None
    if buf < 0:
        return 0
    if buf < 5:
        return 3
    if buf < 10:
        return 5
    if buf < 15:
        return 7
    if buf < 20:
        return 9
    return 10


def s_delta_dte(delta, dte):
    # neutral default 6 when no delta data
    if delta is None or dte is None:
        return 6
    d = abs(delta)
    long_dte = dte > 21
    if d < 0.30:
        return 10 if long_dte else 7
    if d < 0.40:
        return 6 if long_dte else 3
    return 4 if long_dte else 1


def s_delta(delta):
    if delta is None:
        return None
    d = abs(delta)
    if d < 0.20:
        return 10
    if d < 0.30:
        return 8
    if d < 0.40:
        return 6
    if d < 0.50:
        return 3
    return 1


def s_stop_used(pct):
    if pct is None:
        return None
    if pct < 20:
        return 10
    if pct < 40:
        return 8
    if pct < 60:
        return 5
    if pct < 80:
        return 3
    if pct < 100:
        return 1
    return 0


def s_theta_recovery(days):
    if days is None:
        return None
    if days < 7:
        return 10
    if days < 14:
        return 8
    if days < 21:
        return 6
    if days < 30:
        return 4
    return 2


def s_dte(dte):
    if dte is None:
        return None
    if dte <= 0:
        return 0
    if dte > 30:
        return 10
    if dte > 21:
        return 7
    if dte > 14:
        return 5
    if dte > 7:
        return 3
    return 1


def s_strike_buffer(sbuf):
    if sbuf is None:
        return None
    if sbuf <= 0:
        return 0
    if sbuf > 15:
        return 10
    if sbuf > 8:
        return 7
    if sbuf > 3:
        return 4
    return 2


def s_delta_drift(drift):
    if drift is None:
        return None
    d = abs(drift)
    if d < 0.05:
        return 9
    if d < 0.10:
        return 7
    if d < 0.20:
        return 4
    return 1


def s_ivr(ivr):
    # context only, not weighted
    if ivr is None:
        return None
    if ivr > 70:
        return 8
    if ivr > 40:
        return 5
    return 3


# weights (sum to 1.05 in spec; renormalised at composite time)
WEIGHTS = {
    "be_buffer": 0.25,
    "delta_dte": 0.20,
    "delta": 0.15,
    "stop_used": 0.15,
    "theta": 0.10,
    "dte": 0.10,
    "strike_buffer": 0.05,
    "delta_drift": 0.05,
}


def _fmt(x, suffix="", dash="—", nd=1):
    if x is None:
        return dash
    return f"{x:.{nd}f}{suffix}"


def gather_csp(conn, ticker=None):
    sql = """
        SELECT p.id, p.ticker, p.company_name, p.net_premium, p.total_contracts,
               p.breakeven_low, p.earnings_date,
               l.strike, l.open_price, l.contracts, l.expiration,
               c.spot_price, c.pnl_unrealized, c.delta, c.delta_vs_entry,
               c.dte_now, c.theta, c.iv_rank, c.checked_at
        FROM positions p
        JOIN legs l ON l.position_id = p.id AND l.leg_role = 'SHORT_PUT'
        LEFT JOIN checks c ON c.id = (
            SELECT id FROM checks WHERE position_id = p.id AND data_quality = 'GOOD'
            ORDER BY checked_at DESC LIMIT 1
        )
        WHERE p.status = 'OPEN' AND p.strategy = 'CSP'
    """
    args = []
    if ticker:
        sql += " AND p.ticker = ?"
        args.append(ticker.upper())
    sql += " ORDER BY p.ticker"
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    for r in rows:
        strike = r["strike"]
        prem_share = r["open_price"]  # premium per share collected
        # breakeven: prefer stored, else strike - premium/share (CSP)
        be = r["breakeven_low"]
        if be is None and strike is not None and prem_share is not None:
            be = strike - prem_share
        r["be"] = be
        spot = r["spot_price"]
        r["be_buffer_pct"] = (spot - be) / spot * 100 if (spot and be) else None
        r["strike_buffer_pct"] = (spot - strike) / spot * 100 if (spot and strike) else None
        nprem = r["net_premium"]
        # HELM-036 4b: bound recorded P&L to structural limits (DB max_loss/max_profit are NULL for CSP -> compute in-loop)
        _ctr = r["contracts"] or 1
        _mp = nprem
        _ml = (strike * 100 * _ctr - nprem) if (strike is not None and nprem is not None) else None
        pnl, r["pnl_source"] = _pnl_pick(None, r["pnl_unrealized"], None, _ml, _mp)
        r["pnl_display"] = pnl
        r["stop_used_pct"] = (max(0.0, -pnl) / nprem * 100) if (nprem and pnl is not None) else None
        # theta recovery days: loss / daily theta income
        theta = r["theta"]
        contracts = r["contracts"] or 1
        if theta is None or pnl is None:
            r["recovery_days"] = None
        else:
            loss = max(0.0, -pnl)
            income = abs(theta) * 100 * contracts
            r["recovery_days"] = (loss / income) if income > 0 else 0.0
        r["itm"] = (spot is not None and strike is not None and spot < strike)
        r["below_be"] = (spot is not None and be is not None and spot < be)
    return rows


def score_position(r):
    scores = {
        "be_buffer": s_be_buffer(r["be_buffer_pct"]),
        "delta_dte": s_delta_dte(r["delta"], r["dte_now"]),
        "delta": s_delta(r["delta"]),
        "stop_used": s_stop_used(r["stop_used_pct"]),
        "theta": s_theta_recovery(r["recovery_days"]),
        "dte": s_dte(r["dte_now"]),
        "strike_buffer": s_strike_buffer(r["strike_buffer_pct"]),
        "delta_drift": s_delta_drift(r["delta_vs_entry"]),
    }
    num = den = 0.0
    for k, sc in scores.items():
        if sc is None:
            continue
        w = WEIGHTS[k]
        num += w * sc
        den += w
    composite = (num / den * 10) if den > 0 else None
    band, band_color = composite_band(composite)
    return {"scores": scores, "composite": composite, "band": band,
            "band_color": band_color, "ivr_score": s_ivr(r["iv_rank"])}


def guidance(r, scored):
    """Composite-aware guidance; ITM-but-above-b/e gets its own message."""
    if r["below_be"]:
        return ("red", "Below breakeven — spot is under your cost basis. "
                "Defensive: roll down/out or close.")
    if r["itm"]:  # ITM but above breakeven
        return ("amber", "ITM but above breakeven — assignment risk into expiry, "
                "yet still net-profitable. Roll out or accept assignment.")
    c = scored["composite"]
    if c is None:
        return ("gray", "Insufficient data to score — run a fresh check.")
    # Gamma danger zone: high delta + low DTE — losses can accelerate faster than theta can offset
    delta = r.get("delta")
    dte = r.get("dte_now")
    if delta is not None and abs(delta) > 0.35 and dte is not None and dte < 21:
        return ("red", "Gamma danger zone — delta above 0.35 with under 21 days remaining. "
                "Small moves will have outsized impact. Consider closing or rolling now.")
    if c >= 70:
        return ("green", "Healthy — cushion intact. Hold and let theta work.")
    if c >= 40:
        return ("amber", "Watch — cushion thinning or stop partly used. Monitor closely.")
    return ("red", "At risk — multiple stress signals. Review for defensive action.")


# ── HTML rendering ───────────────────────────────────────────────────────────
import html as _html

CSS = """
* { box-sizing: border-box; }
body {
  margin: 0; background: #faf9f6; color: #2a2a28;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.mono { font-family: ui-monospace, "SF Mono", Menlo, Monaco, "Roboto Mono", monospace; }
.wrap { max-width: 1180px; margin: 0 auto; padding: 28px 24px 64px; }
a { color: inherit; text-decoration: none; }

.top { display: flex; align-items: baseline; justify-content: space-between;
       border-bottom: 1px solid #e7e5dd; padding-bottom: 16px; margin-bottom: 22px; }
.brand { font-size: 19px; font-weight: 680; letter-spacing: -0.01em; }
.brand .dim { color: #9b988d; font-weight: 500; }
.asof { font-size: 12px; color: #9b988d; }

.summary { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 26px; }
.stat { background: #fff; border: 1px solid #e7e5dd; border-radius: 12px;
        padding: 13px 18px; min-width: 104px; }
.stat .k { font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: #9b988d; }
.stat .v { font-size: 25px; font-weight: 660; margin-top: 3px; letter-spacing: -0.02em; }
.dist { display: flex; gap: 6px; align-items: center; margin-top: 9px; }
.pip { font-size: 12px; font-weight: 600; padding: 2px 9px; border-radius: 20px; border: 1px solid; }

.keyline { display: flex; gap: 16px; flex-wrap: wrap; align-items: center; font-size: 12px; color: #6b685e; margin: 32px 0 0; }
.keyitem { display: inline-flex; align-items: center; gap: 6px; }
.keyline .sep { color: #c9c6bd; }
.keyline .keynote { color: #9b988d; }
.sqr { width: 11px; height: 11px; border-radius: 3px; display: inline-block; flex: none; }
.vardefs-title { font-size: 15px; font-weight: 680; letter-spacing: -0.01em; margin: 22px 0 14px; }
.vgrid { display: grid; grid-template-columns: 1fr; gap: 12px; }
@media (min-width: 760px) { .vgrid { grid-template-columns: 1fr 1fr; } }
.vcard { background: #fff; border: 1px solid #e7e5dd; border-radius: 12px; padding: 13px 15px; }
.vhead { display: flex; align-items: center; gap: 9px; margin-bottom: 6px; }
.vname { font-size: 12px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; color: #2a2a28; }
.wbadge { font-size: 10px; font-weight: 700; letter-spacing: 0.03em; text-transform: uppercase; padding: 2px 7px; border-radius: 5px; background: #eaf3de; color: #3b6d11; white-space: nowrap; }
.wbadge.ctx { background: #f0efea; color: #7a776d; }
.vdesc { font-size: 12.5px; line-height: 1.45; color: #3f3e3a; margin-bottom: 6px; }
.vcalc { font-size: 11.5px; line-height: 1.4; color: #9b988d; margin-bottom: 8px; }
.vthresh { display: flex; gap: 13px; flex-wrap: wrap; font-size: 11.5px; }
.tchip { display: inline-flex; align-items: center; gap: 5px; }

.cards { display: grid; grid-template-columns: 1fr; gap: 14px; }
@media (min-width: 760px) { .cards { grid-template-columns: 1fr 1fr; } }

.card { background: #fff; border: 1px solid #e7e5dd; border-radius: 14px;
        padding: 16px 16px 14px; transition: box-shadow .15s, transform .15s; display: block; }
.card:hover { box-shadow: 0 6px 22px rgba(60,55,40,.09); transform: translateY(-1px); }

.hrow { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.tk { font-size: 18px; font-weight: 720; letter-spacing: -0.01em; }
.co { font-size: 12px; color: #9b988d; margin-left: -6px; max-width: 150px;
     overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.spacer { flex: 1; }
.comp { font-size: 24px; font-weight: 720; padding: 2px 12px; border-radius: 10px;
        border: 1.5px solid; letter-spacing: -0.02em; line-height: 1.25; }
.comp .of { font-size: 12px; font-weight: 500; opacity: .65; }

.facts { display: flex; gap: 14px; flex-wrap: wrap; align-items: center;
         font-size: 13px; margin-bottom: 13px; }
.fact b { font-weight: 640; }
.fact .lbl { color: #9b988d; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; margin-right: 4px; }
.spot-g { color: #3b6d11; } .spot-a { color: #ba7517; } .spot-r { color: #a32d2d; }
.pill { font-size: 12px; font-weight: 650; padding: 2px 9px; border-radius: 20px; }
.pill-g { background: #eaf3de; color: #27500a; } .pill-r { background: #fcebeb; color: #791f1f; }
.badge-itm { font-size: 10.5px; font-weight: 700; letter-spacing: .05em; padding: 2px 7px;
             border-radius: 5px; background: #fdf0da; color: #854f0b; border: 1px solid #f0b050; }
.pill-src { font-size: 10px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; padding: 2px 6px; border-radius: 5px; background: #efece4; color: #8a8576; border: 1px solid #ddd8cc; }
.pill-src-est { background: #fdf0da; color: #854f0b; border: 1px solid #f0b050; }

.map { display: grid; gap: 6px; }
.maprow { display: grid; gap: 6px; }
.cell { border: 1px solid; border-radius: 9px; padding: 8px 10px; min-height: 56px;
        display: flex; flex-direction: column; justify-content: space-between; }
.cell .clabel { font-size: 10px; font-weight: 650; letter-spacing: .045em; text-transform: uppercase; opacity: .82; }
.cell .cval { font-size: 19px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.05; }
.cell.sm .cval { font-size: 16px; }
.cell .csub { font-size: 10px; opacity: .7; font-weight: 600; }
.cell .schip { font-size: 9.5px; font-weight: 700; opacity: .6; }
.cellfoot { display: flex; justify-content: space-between; align-items: baseline; }

.guide { margin-top: 12px; font-size: 12.5px; line-height: 1.45; padding: 9px 12px;
         border-radius: 9px; border: 1px solid; }
.g-green { background: #f2f8e6; border-color: #b8d97a; color: #27500a; }
.g-amber { background: #faeeda; border-color: #fac775; color: #633806; }
.g-red   { background: #fcebeb; border-color: #f09595; color: #791f1f; }
.g-gray  { background: #f5f5f3; border-color: #dddcd5; color: #5f5e5a; }

.legend { margin-top: 30px; font-size: 11.5px; color: #9b988d; line-height: 1.7; }
.legend b { color: #6b685e; font-weight: 640; }
.back { font-size: 13px; color: #9b988d; margin-bottom: 16px; display: inline-block; }
.back:hover { color: #2a2a28; }
.detail .card { max-width: 720px; }
.empty { background:#fff; border:1px solid #e7e5dd; border-radius:14px; padding:40px; text-align:center; color:#9b988d; }
"""


def _esc(s):
    return _html.escape(str(s)) if s is not None else ""


def _cell(label, value, score, sub="", cls=""):
    c = CELL[cell_color(score)]
    chip = f"{score}/10" if score is not None else "no data"
    style = f"background:{c['bg']};border-color:{c['bd']};color:{c['tx']};"
    valstyle = f"color:{c['vl']};"
    sub_html = f'<span class="csub">{_esc(sub)}</span>' if sub else "<span></span>"
    return (
        f'<div class="cell {cls}" style="{style}">'
        f'<div class="clabel">{_esc(label)}</div>'
        f'<div class="cellfoot">'
        f'<span class="cval mono" style="{valstyle}">{_esc(value)}</span>'
        f'</div>'
        f'<div class="cellfoot">{sub_html}<span class="schip">{chip}</span></div>'
        f'</div>'
    )


def _money(x):
    if x is None:
        return "—"
    return f"${x:,.0f}"


def _render_map(r, sc):
    s = sc["scores"]
    delta = r["delta"]
    dte = r["dte_now"]
    drift = r["delta_vs_entry"]
    # delta x DTE display
    if delta is None:
        ddte_val = f"— · {dte}d" if dte is not None else "—"
        ddte_sub = "neutral (no Δ)"
    else:
        ddte_val = f"{abs(delta):.2f} · {dte}d"
        ddte_sub = "combined"
    rec = r["recovery_days"]
    theta_val = f"{rec:.0f}d" if rec is not None else "—"
    theta_sub = "to recover" if rec is not None else "no greeks"
    row1 = (
        '<div class="maprow" style="grid-template-columns:2.2fr 1.8fr;">'
        + _cell("B/E buffer", _fmt(r["be_buffer_pct"], "%"), s["be_buffer"], "cushion to breakeven")
        + _cell("Delta × DTE", ddte_val, s["delta_dte"], ddte_sub)
        + '</div>'
    )
    row2 = (
        '<div class="maprow" style="grid-template-columns:1fr 1fr 1.4fr;">'
        + _cell("Delta", f"{abs(delta):.2f}" if delta is not None else "—", s["delta"],
                "" if delta is not None else "no greeks", "sm")
        + _cell("1× stop used", _fmt(r["stop_used_pct"], "%", nd=0), s["stop_used"], "of premium", "sm")
        + _cell("Theta / day", theta_val, s["theta"], theta_sub, "sm")
        + '</div>'
    )
    row3 = (
        '<div class="maprow" style="grid-template-columns:1fr 1fr 1fr 1fr 1fr;">'
        + _cell("DTE", f"{dte}d" if dte is not None else "—", s["dte"], "", "sm")
        + _cell("Strike buf", _fmt(r["strike_buffer_pct"], "%"), s["strike_buffer"], "", "sm")
        + _cell("Δ drift", f"{abs(drift):.2f}" if drift is not None else "—", s["delta_drift"],
                "" if drift is not None else "no greeks", "sm")
        + _cell("IVR", _fmt(r["iv_rank"], "", nd=0), sc["ivr_score"], "context", "sm")
        + _premium_cell(r)
        + '</div>'
    )
    return f'<div class="map">{row1}{row2}{row3}</div>'


def _premium_cell(r):
    # reference only, always neutral gray
    c = CELL["gray"]
    style = f"background:{c['bg']};border-color:{c['bd']};color:{c['tx']};"
    return (
        f'<div class="cell sm" style="{style}">'
        f'<div class="clabel">Premium</div>'
        f'<div class="cellfoot"><span class="cval mono" style="color:{c["vl"]};">{_money(r["net_premium"])}</span></div>'
        f'<div class="cellfoot"><span class="csub">collected</span><span class="schip">ref</span></div>'
        f'</div>'
    )


def _spot_class(r):
    if r["below_be"]:
        return "spot-r"
    if r["itm"]:
        return "spot-a"
    return "spot-g"



def _earnings_chip(earnings_date, expiration=None):
    if not earnings_date:
        return ""
    try:
        from datetime import date
        ed = date.fromisoformat(str(earnings_date)[:10])
        label = ed.strftime("%-m/%-d")
    except Exception:
        label = str(earnings_date)[:10]
    warn = ""
    if expiration:
        try:
            from datetime import date as _d
            if _d.fromisoformat(str(earnings_date)[:10]) <= _d.fromisoformat(str(expiration)[:10]):
                warn = "<span class='badge-warn'>⚠ before expiry</span>"
        except Exception:
            pass
    return (
        "<span class='fact'>"
        "<span class='lbl'>Earnings</span>"
        f"<b class='mono'>{label}</b>"
        "</span>" + warn
    )

def _summary_facts(r):
    spot = r["spot_price"]
    pnl = r.get("pnl_display", r["pnl_unrealized"])
    pill_cls = "pill-g" if (pnl is not None and pnl >= 0) else "pill-r"
    pill = f'<span class="pill {pill_cls} mono">{_money(pnl)}</span>' if pnl is not None else ""
    itm = '<span class="badge-itm">ITM</span>' if r["itm"] else ""
    spot_str = f"{spot:.2f}" if spot is not None else "—"
    be_str = f"{r['be']:.2f}" if r["be"] is not None else "—"
    return (
        '<div class="facts">'
        f'<span class="fact"><span class="lbl">Spot</span><b class="mono {_spot_class(r)}">{spot_str}</b></span>'
        f'<span class="fact"><span class="lbl">Strike</span><b class="mono">{r["strike"]:.0f}</b></span>'
        f'<span class="fact"><span class="lbl">B/E</span><b class="mono">{be_str}</b></span>'
        f'<span class="fact"><span class="lbl">DTE</span><b class="mono">{r["dte_now"]}</b></span>'
        + _earnings_chip(r.get('earnings_date'), r.get('expiration'))
        + f'{pill}{itm}'
        '</div>'
    )


def _comp_badge(sc):
    comp = sc["composite"]
    c = CELL[sc["band_color"]]
    val = f"{comp:.0f}" if comp is not None else "—"
    style = f"background:{c['bg']};border-color:{c['bd']};color:{c['vl']};"
    return f'<span class="comp mono" style="{style}">{val}<span class="of"> /100</span></span>'


def _card(r, sc, link=True):
    g_color, g_text = guidance(r, sc)
    co = _esc(r["company_name"] or "")
    head = (
        '<div class="hrow">'
        f'<span class="tk">{_esc(r["ticker"])}</span>'
        f'<span class="co">{co}</span>'
        '<span class="spacer"></span>'
        f'{_comp_badge(sc)}'
        '</div>'
    )
    body = _summary_facts(r) + _render_map(r, sc)
    guide = f'<div class="guide g-{g_color}">{_esc(g_text)}</div>'
    inner = head + body + guide
    if link:
        return f'<a class="card" href="/health?ticker={_esc(r["ticker"])}">{inner}</a>'
    return f'<div class="card">{inner}</div>'


def _page(title, body):
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_esc(title)}</title><style>{CSS}</style></head>"
        f"<body><div class='wrap'>{body}</div></body></html>"
    )



def _refresh_earnings(conn):
    try:
        import yfinance as yf
        from datetime import date
        today = date.today().isoformat()
        # Fetch for positions where earnings_date is null OR already in the past (stale)
        rows = conn.execute(
            "SELECT id, ticker FROM positions WHERE status='OPEN'"
            " AND (earnings_date IS NULL OR earnings_date < ?)"
            , (today,)
        ).fetchall()
        for pos_id, ticker in rows:
            try:
                cal = yf.Ticker(ticker).calendar
                ed = None
                if isinstance(cal, dict) and 'Earnings Date' in cal:
                    dates = cal['Earnings Date']
                    if dates:
                        ed = str(dates[0])[:10]
                if ed and ed not in ('NaT', 'None', ''):
                    conn.execute('UPDATE positions SET earnings_date=? WHERE id=?', (ed, pos_id))
                else:
                    # No upcoming date found — clear stale value
                    conn.execute('UPDATE positions SET earnings_date=NULL WHERE id=?', (pos_id,))
            except Exception:
                pass
        conn.commit()
    except Exception:
        pass

def render(conn, ticker=None):
    _refresh_earnings(conn)
    rows = gather_csp(conn, ticker)
    lc_rows = gather_longcall(conn, ticker)
    ic_rows = gather_icondor(conn, ticker)
    bps_rows = gather_bearput(conn, ticker)
    if ticker:
        if not rows:
            if lc_rows:
                r = lc_rows[0]
                sc = score_longcall(r)
                asof = (r["checked_at"] or "")[:16].replace("T", " ")
                body = (
                    "<a class='back' href='/health'>\u2190 all positions</a>"
                    f"<div class='top'><div class='brand'>HELM <span class='dim'>\u00b7 {_esc(r['ticker'])} health</span></div>"
                    f"<div class='asof'>checked {asof}</div></div>"
                    f"<div class='detail'>{_card_lc(r, sc, link=False)}</div>"
                    + _legend_lc()
                )
                return _page(f"HELM Health \u00b7 {r['ticker']}", body)
        if bps_rows:
            ic_r = bps_rows[0]; ic_sc = score_bearput(ic_r)
            body = (f"<a class='back' href='/health'>\u2190 all positions</a>"
                    + _card_bps(ic_r, ic_sc, link=False)
                    + _legend_bps())
            return _page(f"HELM Health \u00b7 {ic_r['ticker']}", body)
            body = (f"<a class='back' href='/health'>\u2190 all positions</a>"
                    f"<div class='empty'>No open position found for <b>{_esc(ticker.upper())}</b>.</div>")
            return _page(f"HELM Health \u00b7 {ticker.upper()}", body)
        if ic_rows:
            ic_r = ic_rows[0]; ic_sc = score_icondor(ic_r)
            body = (f"<a class='back' href='/health'>\u2190 all positions</a>"
                    + _card_ic(ic_r, ic_sc, link=False)
                    + _legend_ic())
            return _page(f"HELM Health \u00b7 {ic_r['ticker']}", body)
            body = (f"<a class='back' href='/health'>\u2190 all positions</a>"
                    f"<div class='empty'>No open position found for <b>{_esc(ticker.upper())}</b>.</div>")
            return _page(f"HELM Health \u00b7 {ticker.upper()}", body)
        r = rows[0]
        sc = score_position(r)
        asof = (r["checked_at"] or "")[:16].replace("T", " ")
        body = (
            f"<a class='back' href='/health'>← all positions</a>"
            f"<div class='top'><div class='brand'>HELM <span class='dim'>· {_esc(r['ticker'])} health</span></div>"
            f"<div class='asof'>checked {asof}</div></div>"
            f"<div class='detail'>{_card(r, sc, link=False)}</div>"
            + _legend()
        )
        return _page(f"HELM Health · {r['ticker']}", body)

    # portfolio view
    scored = [(r, score_position(r)) for r in rows]
    scored.sort(key=lambda t: (t[1]["composite"] is None, t[1]["composite"] or 0))
    n = len(scored)
    comps = [t[1]["composite"] for t in scored if t[1]["composite"] is not None]
    avg = (sum(comps) / len(comps)) if comps else None
    nh = sum(1 for _, s in scored if s["band_color"] == "green")
    nw = sum(1 for _, s in scored if s["band_color"] == "amber")
    nr = sum(1 for _, s in scored if s["band_color"] == "red")
    asof = ""
    for r, _ in scored:
        if r["checked_at"]:
            asof = r["checked_at"][:16].replace("T", " ")
            break
    top = (
        "<div class='top'><div class='brand'>HELM <span class='dim'>· CSP health map</span></div>"
        f"<div class='asof'>{n} positions · checked {asof}</div></div>"
    )
    avg_str = f"{avg:.0f}" if avg is not None else "—"
    summary = (
        "<div class='summary'>"
        f"<div class='stat'><div class='k'>Positions</div><div class='v mono'>{n}</div></div>"
        f"<div class='stat'><div class='k'>Avg composite</div><div class='v mono'>{avg_str}</div></div>"
        "<div class='stat'><div class='k'>Distribution</div><div class='dist'>"
        f"<span class='pip' style='background:#eaf3de;border-color:#97c459;color:#27500a;'>{nh} healthy</span>"
        f"<span class='pip' style='background:#faeeda;border-color:#fac775;color:#633806;'>{nw} watch</span>"
        f"<span class='pip' style='background:#fcebeb;border-color:#f09595;color:#791f1f;'>{nr} at risk</span>"
        "</div></div></div>"
    )
    cards = "<div class='cards'>" + "".join(_card(r, sc) for r, sc in scored) + "</div>"
    # Long Call section (if any)
    lc_scored = [(r, score_longcall(r)) for r in lc_rows]
    lc_scored.sort(key=lambda t: (t[1]["composite"] is None, t[1]["composite"] or 0))
    lc_section = ""
    if lc_scored:
        lc_section = (
            "<div style='font-size:17px;font-weight:680;letter-spacing:-0.01em;"
            "margin:32px 0 14px;padding-top:24px;border-top:1px solid #e7e5dd;'>"
            "Long Calls</div>"
            "<div class='cards'>" + "".join(_card_lc(r, sc) for r, sc in lc_scored) + "</div>"
            + _legend_lc()
        )
    ic_scored = [(r, score_icondor(r)) for r in ic_rows]
    ic_scored.sort(key=lambda t: (t[1]["composite"] is None, -(t[1]["composite"] or 0)))
    ic_section = ""
    if ic_scored:
        ic_section = (
            "<div style='font-size:.75rem;font-weight:600;color:var(--fg);opacity:.6;"
            "text-transform:uppercase;letter-spacing:.08em;padding:.25rem 0 .5rem;'>Iron Condors</div>"
            + "<div class='cards'>"
            + "".join(_card_ic(r, sc) for r, sc in ic_scored)
            + "</div>"
            + _legend_ic()
        )
    bps_scored = [(r, score_bearput(r)) for r in bps_rows]
    bps_scored.sort(key=lambda t: (t[1]["composite"] is None, -(t[1]["composite"] or 0)))
    bps_section = ""
    if bps_scored:
        bps_section = (
            "<div style='font-size:.75rem;font-weight:600;color:var(--fg);opacity:.6;"
            "text-transform:uppercase;letter-spacing:.08em;padding:.25rem 0 .5rem;'>Bear Put Spreads</div>"
            + "<div class='cards'>"
            + "".join(_card_bps(r, sc) for r, sc in bps_scored)
            + "</div>"
            + _legend_bps()
        )
    return _page("HELM · Portfolio Health", top + summary + cards + _legend() + lc_section + ic_section + bps_section)


# ── Long Call scoring ────────────────────────────────────────────────────────

LC_WEIGHTS = {
    "lc_buffer":     0.25,
    "lc_delta":      0.20,
    "lc_theta":      0.25,
    "lc_dte":        0.15,
    "lc_iv_change":  0.10,
    "stop_used":     0.05,
}


def s_lc_buffer(buf_pct):
    """% stock needs to rise to reach strike. Negative = already ITM (best)."""
    if buf_pct is None:
        return None
    if buf_pct <= 0:
        return 10   # ITM
    if buf_pct < 2:
        return 8
    if buf_pct < 5:
        return 6
    if buf_pct < 10:
        return 4
    if buf_pct < 15:
        return 2
    return 1        # >=15% OTM


def s_lc_delta(delta):
    """Long call delta: want HIGH (stock moving in our favor)."""
    if delta is None:
        return None
    d = abs(delta)
    if d >= 0.70:
        return 10
    if d >= 0.60:
        return 8
    if d >= 0.50:
        return 7
    if d >= 0.40:
        return 5
    if d >= 0.30:
        return 3
    return 1


def s_lc_theta(decay_pct):
    """Theta/day as % of option value. Buyer pays this — lower is better."""
    if decay_pct is None:
        return None
    if decay_pct < 1.0:
        return 10
    if decay_pct < 1.5:
        return 8
    if decay_pct < 2.0:
        return 6
    if decay_pct < 2.5:
        return 4
    if decay_pct < 3.0:
        return 3
    return 1


def s_lc_dte(dte):
    """Long call DTE: more is better (time = optionality). Spec-exact buckets."""
    if dte is None:
        return None
    if dte <= 0:
        return 0
    if dte > 60:
        return 10
    if dte > 45:
        return 8
    if dte > 30:
        return 6
    if dte > 21:
        return 4
    if dte > 7:
        return 2
    return 0


def s_lc_time_value(tv_pct):
    """Time value as % of option price. Lower = more intrinsic, less decay risk."""
    if tv_pct is None:
        return None
    if tv_pct < 20:
        return 10
    if tv_pct < 35:
        return 7
    if tv_pct < 50:
        return 5
    if tv_pct < 70:
        return 3
    return 1


def s_lc_iv_change(chg):
    """IV change vs entry. Rising IV benefits the long buyer."""
    if chg is None:
        return None
    if chg >= 0.05:
        return 9
    if chg >= 0:
        return 6
    if chg >= -0.05:
        return 4
    return 2


def gather_longcall(conn, ticker=None):
    sql = """
        SELECT p.id, p.ticker, p.company_name, p.net_premium, p.total_contracts,
               p.earnings_date,
               l.strike, l.open_price, l.contracts, l.expiration,
               c.spot_price, c.pnl_unrealized, c.delta, c.theta,
               c.iv_current, c.iv_vs_entry, c.dte_now, c.current_price,
               c.checked_at
        FROM positions p
        JOIN legs l ON l.position_id = p.id AND l.leg_role = 'LONG_CALL'
        LEFT JOIN checks c ON c.id = (
            SELECT id FROM checks WHERE position_id = p.id AND data_quality = 'GOOD'
            ORDER BY checked_at DESC LIMIT 1
        )
        WHERE p.status = 'OPEN' AND p.strategy = 'LONG_CALL'
    """
    args = []
    if ticker:
        sql += " AND p.ticker = ?"
        args.append(ticker.upper())
    sql += " ORDER BY p.ticker"
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    for r in rows:
        spot = r["spot_price"]
        strike = r["strike"]
        cur_price = r["current_price"]
        # buffer to strike: % stock needs to rise (negative = ITM)
        r["buffer_to_strike_pct"] = ((strike - spot) / spot * 100) if (spot and strike) else None
        # intrinsic value per share
        r["intrinsic"] = max(0.0, spot - strike) if (spot and strike) else None
        # time value % of current option price
        if cur_price and cur_price > 0 and r["intrinsic"] is not None:
            tv = max(0.0, cur_price - r["intrinsic"])
            r["time_value_pct"] = tv / cur_price * 100
        else:
            r["time_value_pct"] = None
        # theta decay rate: |theta|/day as % of option price
        theta = r["theta"]
        if theta and cur_price and cur_price > 0:
            r["theta_decay_pct"] = abs(theta) / cur_price * 100
        else:
            r["theta_decay_pct"] = None
        # stop used (premium paid = max loss for a long)
        nprem = r["net_premium"]
        # HELM-036 4b: bound recorded P&L; long-call max loss = premium paid, upside uncapped
        pnl, r["pnl_source"] = _pnl_pick(None, r["pnl_unrealized"], None, (abs(nprem) if nprem else None), None)
        r["pnl_display"] = pnl
        r["stop_used_pct"] = (max(0.0, -pnl) / abs(nprem) * 100) if (nprem and pnl is not None) else None
        # itm flag (for long call: spot > strike = ITM = good)
        r["itm"] = (spot is not None and strike is not None and spot > strike)
    return rows


def score_longcall(r):
    scores = {
        "lc_buffer":     s_lc_buffer(r["buffer_to_strike_pct"]),
        "lc_delta":      s_lc_delta(r["delta"]),
        "lc_theta":      s_lc_theta(r["theta_decay_pct"]),
        "lc_dte":        s_lc_dte(r["dte_now"]),
        "lc_iv_change":  s_lc_iv_change(r["iv_vs_entry"]),
        "stop_used":     s_stop_used(r["stop_used_pct"]),
    }
    num = den = 0.0
    for k, sc in scores.items():
        if sc is None:
            continue
        w = LC_WEIGHTS[k]
        num += w * sc
        den += w
    composite = (num / den * 10) if den > 0 else None
    band, band_color = composite_band(composite)
    return {"scores": scores, "composite": composite, "band": band,
            "band_color": band_color}


def guidance_longcall(r, scored):
    c = scored["composite"]
    buf = r["buffer_to_strike_pct"]
    dte = r["dte_now"]
    if c is None:
        return ("gray", "Insufficient data \u2014 run a fresh check.")
    # Gamma danger zone: near-ATM + low DTE = binary outcome territory
    if buf is not None and abs(buf) < 5 and dte is not None and dte < 21:
        return ("red", "Binary territory — near the strike with under 21 days left. "
                "The option needs a sharp move soon or expires worthless. "
                "Decide: hold with conviction or close to preserve remaining value.")
    if c >= 70:
        if r["itm"]:
            return ("green", "Healthy and ITM \u2014 stock on your side. Monitor theta and consider rolling up to lock in gains.")
        return ("green", "Healthy \u2014 stock tracking well. Hold and let the position develop.")
    if c >= 40:
        if buf is not None and buf > 10:
            return ("amber", "Watch \u2014 stock still needs to move. Theta working against you daily.")
        return ("amber", "Watch \u2014 manageable but monitor theta decay and IV closely.")
    if dte is not None and dte < 21:
        return ("red", "At risk \u2014 limited time remaining. Consider closing or rolling to avoid full premium loss.")
    return ("red", "At risk \u2014 multiple stress signals. Review for defensive action.")


def _render_map_lc(r, sc):
    s = sc["scores"]
    delta = r["delta"]
    dte = r["dte_now"]
    buf = r["buffer_to_strike_pct"]
    # buffer display
    if buf is None:
        buf_val, buf_sub = "\u2014", "no data"
    elif buf <= 0:
        buf_val, buf_sub = f"{abs(buf):.1f}% ITM", "above strike"
    else:
        buf_val, buf_sub = f"{buf:.1f}% to go", "needs to rise"
    # delta
    delta_val = f"{abs(delta):.2f}" if delta is not None else "\u2014"
    delta_sub = "want \u2265 0.50" if delta is not None else "no greeks"
    # theta decay
    td = r["theta_decay_pct"]
    theta_val = f"{td:.1f}%/d" if td is not None else "\u2014"
    theta_sub = "daily cost" if td is not None else "no greeks"
    # DTE
    dte_val = f"{dte}d" if dte is not None else "\u2014"
    # IV change
    ivc = r["iv_vs_entry"]
    if ivc is not None:
        ivc_val = (f"+{ivc:.3f}" if ivc >= 0 else f"{ivc:.3f}")
    else:
        ivc_val = "\u2014"
    ivc_sub = "vs entry IV"
    # Row 1: buffer to strike | delta
    row1 = (
        '<div class="maprow" style="grid-template-columns:2.2fr 1.8fr;">'
        + _cell("Buffer to strike", buf_val, s["lc_buffer"], buf_sub)
        + _cell("Delta \u03b4", delta_val, s["lc_delta"], delta_sub)
        + "</div>"
    )
    # Row 2: theta decay | DTE | IV change
    row2 = (
        '<div class="maprow" style="grid-template-columns:1fr 1fr 1.4fr;">'
        + _cell("Theta decay", theta_val, s["lc_theta"], theta_sub, "sm")
        + _cell("DTE", dte_val, s["lc_dte"], "", "sm")
        + _cell("IV change", ivc_val, s["lc_iv_change"], ivc_sub, "sm")
        + "</div>"
    )
    # Row 3: time value % | stop used | intrinsic | premium paid
    tv = r["time_value_pct"]
    tv_val = f"{tv:.0f}%" if tv is not None else "\u2014"
    stop = r["stop_used_pct"]
    stop_val = f"{stop:.0f}%" if stop is not None else "\u2014"
    intrinsic = r["intrinsic"]
    intr_val = f"${intrinsic:.2f}" if intrinsic is not None else "\u2014"
    pc = CELL["gray"]
    pstyle = f"background:{pc['bg']};border-color:{pc['bd']};color:{pc['tx']};"
    prem_cell = (
        f"<div class='cell sm' style='{pstyle}'>"
        f"<div class='clabel'>Premium paid</div>"
        f"<div class='cellfoot'><span class='cval mono' style='color:{pc['vl']};'>{_money(abs(r['net_premium']))}</span></div>"
        f"<div class='cellfoot'><span class='csub'>total cost</span><span class='schip'>ref</span></div>"
        "</div>"
    )
    ic = CELL["gray"]
    istyle = f"background:{ic['bg']};border-color:{ic['bd']};color:{ic['tx']};"
    intr_cell = (
        f"<div class='cell sm' style='{istyle}'>"
        f"<div class='clabel'>Intrinsic</div>"
        f"<div class='cellfoot'><span class='cval mono' style='color:{ic['vl']};'>{_esc(intr_val)}</span></div>"
        f"<div class='cellfoot'><span class='csub'>per share</span><span class='schip'>ref</span></div>"
        "</div>"
    )
    row3 = (
        '<div class="maprow" style="grid-template-columns:1fr 1fr 1fr;">'
        + _cell("1\u00d7 stop used", stop_val, s["stop_used"], "of premium", "sm")
        + intr_cell
        + prem_cell
        + "</div>"
    )
    return f"<div class='map'>{row1}{row2}{row3}</div>"


def _summary_facts_lc(r):
    spot = r["spot_price"]
    pnl = r.get("pnl_display", r["pnl_unrealized"])
    strike = r["strike"]
    itm = r["itm"]
    spot_str = f"{spot:.2f}" if spot is not None else "\u2014"
    pill_cls = "pill-g" if (pnl is not None and pnl >= 0) else "pill-r"
    pill = f"<span class='pill {pill_cls} mono'>{_money(pnl)}</span>" if pnl is not None else ""
    itm_badge = "<span class='badge-itm'>ITM</span>" if itm else ""
    buf = r.get("buffer_to_strike_pct")
    spot_cls = "spot-g" if itm else ("spot-a" if (buf is not None and buf < 5) else "spot-r")
    return (
        "<div class='facts'>"
        f"<span class='fact'><span class='lbl'>Spot</span><b class='mono {spot_cls}'>{spot_str}</b></span>"
        f"<span class='fact'><span class='lbl'>Strike</span><b class='mono'>{strike:.0f}</b></span>"
        f"<span class='fact'><span class='lbl'>DTE</span><b class='mono'>{r['dte_now']}</b></span>"
        + _earnings_chip(r.get('earnings_date'), r.get('expiration'))
        + f"{pill}{itm_badge}"
        + "</div>"
    )


def _card_lc(r, sc, link=True):
    g_color, g_text = guidance_longcall(r, sc)
    co = _esc(r["company_name"] or "")
    head = (
        '<div class="hrow">'
        f'<span class="tk">{_esc(r["ticker"])}</span>'
        f'<span class="co">{co}</span>'
        '<span class="spacer"></span>'
        f'{_comp_badge(sc)}'
        '</div>'
    )
    body = _summary_facts_lc(r) + _render_map_lc(r, sc)
    guide = f'<div class="guide g-{g_color}">{_esc(g_text)}</div>'
    inner = head + body + guide
    if link:
        return f'<a class="card" href="/health?ticker={_esc(r["ticker"])}">{inner}</a>'
    return f'<div class="card">{inner}</div>'


def _legend_lc():
    SQ = {
        "green": ("#6aa329", "#3b6d11"),
        "amber": ("#e0a64a", "#ba7517"),
        "red":   ("#cf6b6b", "#a32d2d"),
        "gray":  ("#b9b7ae", "#7a776d"),
    }

    def chip(ck, word, thresh):
        fill, tx = SQ[ck]
        return (
            f"<span class='tchip'><span class='sqr' style='background:{fill}'></span>"
            f"<span style='color:{tx}'><b>{_esc(word)}</b> {_esc(thresh)}</span></span>"
        )

    defs = [
        ("Buffer to strike", "Weight 25%", False,
         "How far stock needs to rise to reach the strike. Negative = ITM (has intrinsic value).",
         "(strike \u2212 spot) \u00f7 spot \u00d7 100. Negative means ITM.",
         [("green","green","ITM or <2% away"),("amber","amber","2\u201310%"),("red","red",">10% to go")]),
        ("Delta \u03b4", "Weight 20%", False,
         "Long call delta: the higher the better. Rising means the stock is moving your way.",
         "Absolute delta from IBKR. For long calls, want \u2265 0.50.",
         [("green","green","\u22650.60"),("amber","amber","0.40\u20130.60"),("red","red","<0.40")]),
        ("Theta decay", "Weight 15%", False,
         "Daily time decay as % of option value. You pay this every day \u2014 lower is better.",
         "|theta| \u00f7 current option price \u00d7 100. <1%/d good, >3%/d bad.",
         [("green","green","<1.5%/d"),("amber","amber","1.5\u20132.5%/d"),("red","red",">2.5%/d")]),
        ("DTE", "Weight 15%", False,
         "Days to expiration. More time = more optionality. Opposite urgency from CSP.",
         "Calendar days to expiry. >60d comfortable, <21d high-urgency.",
         [("green","green",">45d"),("amber","amber","21\u201345d"),("red","red","<21d")]),
        ("IV change", "Weight 10%", False,
         "Change in implied volatility vs entry. Rising IV increases option value (good for buyer).",
         "current IV \u2212 entry IV. Requires entry snapshot (helm open --confirm).",
         [("green","green","rising \u22650.05"),("amber","amber","flat"),("red","red","falling <\u22120.05")]),
        ("1\u00d7 stop used", "Weight 5%", False,
         "How much of the premium you paid has been lost. 1\u00d7 stop = full premium paid.",
         "unrealised loss \u00f7 premium paid \u00d7 100.",
         [("green","green","<40%"),("amber","amber","40\u201370%"),("red","red",">80%")]),
        ("Intrinsic", "Reference", True,
         "The in-the-money value: max(0, spot \u2212 strike) per share. Zero if OTM.",
         "max(0, spot \u2212 strike).",
         [("green","green","positive (ITM)"),("gray","gray","zero (OTM)")]),
        ("Premium paid", "Reference", True,
         "Total cost of the position at entry. This is your maximum loss.",
         "net_premium from position record.",
         []),
    ]

    cards = []
    for name, wlabel, is_ctx, desc, calc, chips in defs:
        badge_cls = "wbadge ctx" if is_ctx else "wbadge"
        ch = "".join(chip(*c) for c in chips)
        cards.append(
            "<div class='vcard'>"
            f"<div class='vhead'><span class='vname'>{_esc(name)}</span>"
            f"<span class='{badge_cls}'>{_esc(wlabel)}</span></div>"
            f"<div class='vdesc'>{_esc(desc)}</div>"
            f"<div class='vcalc'>Calc: {_esc(calc)}</div>"
            f"<div class='vthresh'>{ch}</div>"
            "</div>"
        )

    keyline = (
        "<div class='keyline'>"
        "<span class='keyitem'><span class='sqr' style='background:#6aa329'></span>70+ healthy</span>"
        "<span class='keyitem'><span class='sqr' style='background:#e0a64a'></span>40\u201369 watch</span>"
        "<span class='keyitem'><span class='sqr' style='background:#cf6b6b'></span>&lt;40 concern</span>"
        "<span class='keyitem'><span class='sqr' style='background:#b9b7ae'></span>no data</span>"
        "<span class='sep'>\u00b7</span><span class='keynote'>cell size = variable weight</span>"
        "</div>"
    )
    grid = "<div class='vgrid'>" + "".join(cards) + "</div>"
    return (keyline
            + "<div class='vardefs-title'>Long Call \u2014 variable definitions &amp; scoring</div>"
            + grid)


# Clean Iron Condor health map block for insertion into health.py
# Requires: _cell, _money, _esc, _comp_badge, _page, CELL, composite_band, s_stop_used
# Insert before: def _legend():

import math as _ic_math
from scipy.stats import norm as _ic_norm

IC_WEIGHTS = {
    "put_be_buffer":  0.20,
    "call_be_buffer": 0.20,
    "net_delta":      0.15,
    "put_delta":      0.10,
    "call_delta":     0.10,
    "dte":            0.10,
    "max_profit_pct": 0.10,
    "stop_used":      0.05,
}


def _bs_delta_ic(spot, strike, T_days, iv, r=0.043, option_type="call"):
    if not (spot and strike and T_days and T_days > 0 and iv and iv > 0):
        return None
    try:
        T = T_days / 365.0
        d1 = (_ic_math.log(spot / strike) + (r + 0.5 * iv**2) * T) / (iv * _ic_math.sqrt(T))
        return _ic_norm.cdf(d1) if option_type.upper() in ("C", "CALL") else _ic_norm.cdf(d1) - 1
    except Exception:
        return None


def s_ic_be_buffer(pct):
    if pct is None: return None
    if pct <= 0:  return 0
    if pct < 1:   return 1
    if pct < 3:   return 2
    if pct < 5:   return 4
    if pct < 7:   return 6
    if pct < 10:  return 6
    return 10


def s_ic_net_delta(d):
    if d is None: return None
    if d < 0.05:  return 10
    if d < 0.10:  return 7
    if d < 0.20:  return 4
    return 1


def s_ic_leg_delta(d):
    if d is None: return None
    if d < 0.15:  return 10
    if d < 0.20:  return 8
    if d < 0.25:  return 6
    if d < 0.30:  return 4
    if d < 0.35:  return 2
    return 1


def s_ic_dte(dte):
    if dte is None: return None
    if dte <= 7:   return 0
    if dte <= 21:  return 2
    if dte <= 30:  return 6
    if dte <= 45:  return 8
    if dte <= 60:  return 9
    return 10


def s_ic_max_profit(pct):
    if pct is None: return None
    if pct < 0:   return 0
    if pct < 10:  return 2
    if pct < 25:  return 4
    if pct < 40:  return 6
    if pct < 50:  return 8
    return 10


def gather_icondor(conn, ticker=None):
    sql = """
        SELECT p.id, p.ticker, p.company_name, p.net_premium, p.total_contracts,
               p.max_loss, p.earnings_date,
               c.spot_price, c.delta, c.theta, c.dte_now,
               c.pnl_unrealized, c.iv_current, c.iv_vs_entry, c.checked_at, c.greeks_source, c.data_quality
        FROM positions p
        LEFT JOIN checks c ON c.id = (
            SELECT id FROM checks WHERE position_id = p.id AND data_quality = 'GOOD'
            ORDER BY checked_at DESC LIMIT 1
        )
        WHERE p.status = 'OPEN' AND p.strategy = 'IRON_CONDOR'
    """
    args = []
    if ticker:
        sql += " AND p.ticker = ?"
        args.append(ticker.upper())
    sql += " ORDER BY p.ticker"
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    for r in rows:
        legs = [dict(l) for l in conn.execute(
            "SELECT leg_role, option_type, direction, strike, open_price, contracts, expiration "
            "FROM legs WHERE position_id = ? ORDER BY strike", (r["id"],)
        ).fetchall()]
        sp  = next((l for l in legs if l["leg_role"] == "SHORT_PUT"),  None)
        sc_ = next((l for l in legs if l["leg_role"] == "SHORT_CALL"), None)
        lp  = next((l for l in legs if l["leg_role"] == "LONG_PUT"),   None)
        lc_ = next((l for l in legs if l["leg_role"] == "LONG_CALL"),  None)
        r["short_put_strike"]  = sp["strike"]  if sp  else None
        r["short_call_strike"] = sc_["strike"] if sc_ else None
        r["long_put_strike"]   = lp["strike"]  if lp  else None
        r["long_call_strike"]  = lc_["strike"] if lc_ else None
        r["expiration"] = sp["expiration"] if sp else (sc_["expiration"] if sc_ else None)
        r["put_spread_width"]  = (sp["strike"] - lp["strike"])   if (sp  and lp)  else None
        r["call_spread_width"] = (lc_["strike"] - sc_["strike"]) if (lc_ and sc_) else None
        net_prem = r["net_premium"]
        contracts = r["total_contracts"]
        nc = (net_prem / (contracts * 100)) if (net_prem and contracts) else None
        r["net_credit_per_share"] = nc
        r["put_breakeven"]  = (sp["strike"]  - nc) if (sp  and nc) else None
        r["call_breakeven"] = (sc_["strike"] + nc) if (sc_ and nc) else None
        spot = r["spot_price"]
        r["put_be_buffer_pct"]  = ((spot - r["put_breakeven"])  / spot * 100) if (spot and r["put_breakeven"])  else None
        r["call_be_buffer_pct"] = ((r["call_breakeven"] - spot) / spot * 100) if (spot and r["call_breakeven"]) else None
        iv  = (r["iv_current"] / 100.0) if r["iv_current"] else None
        dte = r["dte_now"]
        r["short_put_delta"]  = _bs_delta_ic(spot, r["short_put_strike"],  dte, iv, option_type="put")  if iv else None
        r["short_call_delta"] = _bs_delta_ic(spot, r["short_call_strike"], dte, iv, option_type="call") if iv else None
        r["abs_put_delta"]  = abs(r["short_put_delta"])  if r["short_put_delta"]  is not None else None
        r["abs_call_delta"] = abs(r["short_call_delta"]) if r["short_call_delta"] is not None else None
        r["abs_net_delta"]  = abs(r["delta"]) if r["delta"] is not None else None
        pnl, r["pnl_source"] = _pnl_pick(r.get("greeks_source"), r["pnl_unrealized"], None, r["max_loss"], net_prem)
        r["pnl_display"] = pnl
        r["max_profit_pct"] = (pnl / net_prem * 100) if (pnl is not None and net_prem) else None
        max_loss = r["max_loss"]
        r["stop_used_pct"] = (max(0.0, -pnl) / max_loss * 100) if (pnl is not None and max_loss and max_loss > 0) else None
        r["in_profit_zone"]   = bool(spot and r["put_breakeven"] and r["call_breakeven"] and r["put_breakeven"] < spot < r["call_breakeven"])
        r["put_side_tested"]  = bool(spot and sp  and spot < sp["strike"])
        r["call_side_tested"] = bool(spot and sc_ and spot > sc_["strike"])
        r["put_strike_dist_pct"]  = ((spot - r["short_put_strike"])  / spot * 100) if (spot and r["short_put_strike"])  else None
        r["call_strike_dist_pct"] = ((r["short_call_strike"] - spot) / spot * 100) if (spot and r["short_call_strike"]) else None
        r["theta_display"] = abs(r["theta"]) if r["theta"] is not None else None
    return rows


def score_icondor(r):
    scores = {
        "put_be_buffer":  s_ic_be_buffer(r["put_be_buffer_pct"]),
        "call_be_buffer": s_ic_be_buffer(r["call_be_buffer_pct"]),
        "net_delta":      s_ic_net_delta(r["abs_net_delta"]),
        "put_delta":      s_ic_leg_delta(r["abs_put_delta"]),
        "call_delta":     s_ic_leg_delta(r["abs_call_delta"]),
        "dte":            s_ic_dte(r["dte_now"]),
        "max_profit_pct": s_ic_max_profit(r["max_profit_pct"]),
        "stop_used":      s_stop_used(r["stop_used_pct"]),
    }
    num = den = 0.0
    for k, sc in scores.items():
        if sc is None:
            continue
        w = IC_WEIGHTS[k]
        num += w * sc
        den += w
    composite = (num / den * 10) if den > 0 else None
    band, band_color = composite_band(composite)
    return {"scores": scores, "composite": composite, "band": band, "band_color": band_color}


def guidance_icondor(r, scored):
    c      = scored["composite"]
    put_d  = r.get("abs_put_delta")
    call_d = r.get("abs_call_delta")
    dte    = r.get("dte_now")
    max_pct = r.get("max_profit_pct")
    # Gamma squeeze: high delta near expiry
    if ((put_d is not None and put_d >= 0.30) or (call_d is not None and call_d >= 0.30)) and dte is not None and dte < 21:
        return ("red", "Gamma squeeze \u2014 threatened side accelerating near expiration. Close immediately.")
    # 50% profit target
    if max_pct is not None and max_pct >= 50:
        return ("green", "At or past 50% profit target \u2014 close this position and redeploy capital.")
    if c is None:
        return ("gray", "Insufficient data \u2014 run a fresh check.")
    # Tested sides
    if r.get("call_side_tested"):
        return ("red", "Call side tested \u2014 stock above short call. Roll or close immediately.")
    if r.get("put_side_tested"):
        return ("red", "Put side tested \u2014 stock below short put. Roll or close immediately.")
    # Leg delta warnings
    if put_d is not None and put_d >= 0.30:
        return ("amber", "Put side at action threshold \u2014 delta above 0.30. Roll untested (call) side in or close.")
    if call_d is not None and call_d >= 0.30:
        return ("amber", "Call side at action threshold \u2014 delta above 0.30. Roll untested (put) side in or close.")
    if put_d is not None and put_d >= 0.25:
        return ("amber", "Put side at watch threshold \u2014 monitor closely. Roll untested side if move continues.")
    if call_d is not None and call_d >= 0.25:
        return ("amber", "Call side at watch threshold \u2014 monitor closely. Roll untested side if move continues.")
    # DTE urgency
    if dte is not None and dte <= 7:
        return ("red", "Under 7 DTE \u2014 close immediately to avoid assignment risk.")
    if dte is not None and dte <= 21:
        return ("amber", "Under 21 DTE \u2014 evaluate closing to avoid gamma risk in the final weeks.")
    # Composite bands
    if c >= 70:
        return ("green", "Healthy \u2014 stock inside profit zone, buffers intact. Theta working in your favor.")
    if c >= 40:
        return ("amber", "Watch \u2014 buffers thinning or delta drifting. Monitor for potential adjustment.")
    return ("red", "At risk \u2014 significant pressure on one or both sides. Consider closing or rolling.")


def _summary_facts_ic(r):
    spot = r["spot_price"]
    pnl  = r.get("pnl_display", r["pnl_unrealized"])
    sp_s = r["short_put_strike"]
    sc_s = r["short_call_strike"]
    spot_str = f"{spot:.2f}" if spot is not None else "\u2014"
    put_d  = r.get("put_strike_dist_pct")
    call_d = r.get("call_strike_dist_pct")
    if r.get("put_side_tested") or r.get("call_side_tested"):
        spot_cls = "spot-r"
    elif (put_d is not None and put_d < 3) or (call_d is not None and call_d < 3):
        spot_cls = "spot-r"
    elif (put_d is not None and put_d < 5) or (call_d is not None and call_d < 5):
        spot_cls = "spot-a"
    else:
        spot_cls = "spot-g"
    pill_cls = "pill-g" if (pnl is not None and pnl >= 0) else "pill-r"
    pill = f"<span class='pill {pill_cls} mono'>{_money(pnl)}</span>" if pnl is not None else ""
    _ps = r.get("pnl_source")
    src = (f"<span class='pill-src{' pill-src-est' if _ps in ('BS est','recorded','n/a') else ''}'>{_ps}</span>") if (pnl is not None and _ps) else ''
    zone_badge = "<span class='badge-itm'>IN ZONE</span>" if r.get("in_profit_zone") else ""
    rng = f"{sp_s:.0f}\u2013{sc_s:.0f}" if (sp_s and sc_s) else "\u2014"
    return (
        "<div class='facts'>"
        f"<span class='fact'><span class='lbl'>Spot</span><b class='mono {spot_cls}'>{spot_str}</b></span>"
        f"<span class='fact'><span class='lbl'>Zone</span><b class='mono'>{rng}</b></span>"
        f"<span class='fact'><span class='lbl'>DTE</span><b class='mono'>{r['dte_now']}</b></span>"
        + _earnings_chip(r.get('earnings_date'), r.get('expiration'))
        + f"{pill}{src}{zone_badge}"
        + "</div>"
    )


def _render_map_ic(r, sc):
    s = sc["scores"]
    pb = r["put_be_buffer_pct"]
    cb = r["call_be_buffer_pct"]
    pb_val = f"{pb:.1f}%" if pb is not None else "\u2014"
    cb_val = f"{cb:.1f}%" if cb is not None else "\u2014"
    pb_be  = r["put_breakeven"]
    cb_be  = r["call_breakeven"]
    pb_sub = f"b/e at ${pb_be:.2f}" if pb_be is not None else "no data"
    cb_sub = f"b/e at ${cb_be:.2f}" if cb_be is not None else "no data"
    row1 = (
        '<div class="maprow" style="grid-template-columns:2fr 2fr;">'
        + _cell("Put b/e buffer",  pb_val, s["put_be_buffer"],  pb_sub)
        + _cell("Call b/e buffer", cb_val, s["call_be_buffer"], cb_sub)
        + "</div>"
    )
    nd = r["abs_net_delta"]
    pd = r["abs_put_delta"]
    cd = r["abs_call_delta"]
    nd_val = f"{nd:.3f}" if nd is not None else "\u2014"
    pd_val = f"{pd:.3f}" if pd is not None else "\u2014"
    cd_val = f"{cd:.3f}" if cd is not None else "\u2014"
    pd_sub = "BS approx" if (pd is not None and r["iv_current"]) else "no data"
    cd_sub = "BS approx" if (cd is not None and r["iv_current"]) else "no data"
    row2 = (
        '<div class="maprow" style="grid-template-columns:1.2fr 1fr 1fr;">'
        + _cell("Net delta \u03b4",   nd_val, s["net_delta"],  "want near zero", "sm")
        + _cell("Short put \u03b4",   pd_val, s["put_delta"],  pd_sub, "sm")
        + _cell("Short call \u03b4",  cd_val, s["call_delta"], cd_sub, "sm")
        + "</div>"
    )
    dte    = r["dte_now"]
    dte_val = f"{dte}d" if dte is not None else "\u2014"
    mp     = r["max_profit_pct"]
    mp_val = f"{mp:.0f}%" if mp is not None else "\u2014"
    su     = r["stop_used_pct"]
    su_val = f"{su:.0f}%" if su is not None else "\u2014"
    gc = CELL["gray"]
    gstyle = f"background:{gc['bg']};border-color:{gc['bd']};color:{gc['tx']};"
    nc_cell = (
        f"<div class='cell sm' style='{gstyle}'>"
        f"<div class='clabel'>Net credit</div>"
        f"<div class='cellfoot'><span class='cval mono' style='color:{gc['vl']};'>{_money(r['net_premium'])}</span></div>"
        f"<div class='cellfoot'><span class='csub'>collected</span><span class='schip'>ref</span></div>"
        "</div>"
    )
    ml = r["max_loss"]
    ml_cell = (
        f"<div class='cell sm' style='{gstyle}'>"
        f"<div class='clabel'>Max loss</div>"
        f"<div class='cellfoot'><span class='cval mono' style='color:{gc['vl']};'>{_money(ml) if ml else chr(0x2014)}</span></div>"
        f"<div class='cellfoot'><span class='csub'>defined risk</span><span class='schip'>ref</span></div>"
        "</div>"
    )
    row3 = (
        '<div class="maprow" style="grid-template-columns:1fr 1fr 1fr 1fr 1fr;">'
        + _cell("DTE",           dte_val, s["dte"],            "",              "sm")
        + _cell("% max profit",  mp_val,  s["max_profit_pct"], "of max profit", "sm")
        + _cell("1\u00d7 stop",  su_val,  s["stop_used"],      "of max loss",   "sm")
        + nc_cell + ml_cell
        + "</div>"
    )
    return f"<div class='map'>{row1}{row2}{row3}</div>"


def _card_ic(r, sc, link=True):
    g_color, g_text = guidance_icondor(r, sc)
    co   = _esc(r["company_name"] or "")
    head = (
        '<div class="hrow">'
        f'<span class="tk">{_esc(r["ticker"])}</span>'
        f'<span class="co">{co}</span>'
        '<span class="spacer"></span>'
        f'{_comp_badge(sc)}'
        '</div>'
    )
    body  = _summary_facts_ic(r) + _render_map_ic(r, sc)
    guide = f'<div class="guide g-{g_color}">{_esc(g_text)}</div>'
    inner = head + body + guide
    if link:
        return f'<a class="card" href="/health?ticker={_esc(r["ticker"])}">{inner}</a>'
    return f'<div class="card">{inner}</div>'


def _legend_ic():
    SQ = {
        "green": ("#6aa329", "#3b6d11"), "amber": ("#e0a64a", "#ba7517"),
        "red":   ("#cf6b6b", "#a32d2d"),
        "gray":   ("#b9b7ae", "#7a776d"),
    }
    def chip(ck, word, thresh):
        fill, tx = SQ[ck]
        return (
            f"<span class='tchip'><span class='sqr' style='background:{fill}'></span>"
            f"<span style='color:{tx}'><b>{_esc(word)}</b> {_esc(thresh)}</span></span>"
        )
    defs = [
        ("Put b/e buffer", "Weight 20%", False,
         "How far spot can fall before crossing the put-side breakeven. Higher = more cushion.",
         "(spot \u2212 put_be) \u00f7 spot \u00d7 100.  put_be \u2261 short_put \u2212 net_credit/share.",
         [("green","green","\u226510%"),("amber","amber","3\u201310%"),("red","red","<3%")]),
        ("Call b/e buffer", "Weight 20%", False,
         "How far spot can rise before crossing the call-side breakeven. Higher = more cushion.",
         "(call_be \u2212 spot) \u00f7 spot \u00d7 100.  call_be \u2261 short_call + net_credit/share.",
         [("green","green","\u226510%"),("amber","amber","3\u201310%"),("red","red","<3%")]),
        ("Net delta \u03b4", "Weight 15%", False,
         "Absolute net delta of the full position. Near zero = balanced. Any drift signals directional exposure.",
         "|sum of all leg deltas| from latest deep check.",
         [("green","green","<0.05"),("amber","amber","0.05\u20130.20"),("red","red","\u22650.20")]),
        ("Short put \u03b4", "Weight 10%", False,
         "Absolute delta of the short put. Rising = gaining assignment probability. Watch: \u22650.25. Act: \u22650.30.",
         "Black-Scholes approximation using spot, put strike, DTE, and IV from latest check.",
         [("green","green","<0.20"),("amber","amber","0.20\u20130.30"),("red","red","\u22650.30")]),
        ("Short call \u03b4", "Weight 10%", False,
         "Absolute delta of the short call. Rising = gaining assignment probability. Watch: \u22650.25. Act: \u22650.30.",
         "Black-Scholes approximation using spot, call strike, DTE, and IV from latest check.",
         [("green","green","<0.20"),("amber","amber","0.20\u20130.30"),("red","red","\u22650.30")]),
        ("DTE", "Weight 10%", False,
         "Days to expiration. Standard practice: close at 21 DTE or 50% profit \u2014 whichever comes first.",
         "Calendar days to expiry. Hard close at 7 DTE to avoid gamma risk and assignment.",
         [("green","green",">30d"),("amber","amber","7\u201321d"),("red","red","\u22647d close now")]),
        ("% max profit", "Weight 10%", False,
         "Percentage of maximum profit already captured. Industry standard close target: 50%.",
         "pnl_unrealized \u00f7 net_premium \u00d7 100. net_premium = total credit collected at open.",
         [("green","green","\u226550% (close)"),("amber","amber","25\u201350%"),("red","red","<10%")]),
        ("1\u00d7 stop", "Weight 5%", False,
         "Unrealised loss as % of max loss. Hard stop rule: 200% of credit received (2\u00d7 credit).",
         "max(0, \u2212pnl) \u00f7 max_loss \u00d7 100.",
         [("green","green","0%"),("amber","amber","<40%"),("red","red","\u226560%")]),
        ("Net credit", "Reference", True,
         "Total premium collected at open. This is your maximum possible profit if both spreads expire worthless.",
         "net_premium from position record.", []),
        ("Max loss", "Reference", True,
         "Maximum defined loss = (spread width \u2212 net credit) \u00d7 contracts \u00d7 100.",
         "max_loss from position record.", []),
    ]
    cards = []
    for name, wlabel, is_ctx, desc, calc, chips in defs:
        badge_cls = "wbadge ctx" if is_ctx else "wbadge"
        ch = "".join(chip(*c) for c in chips)
        cards.append(
            "<div class='vcard'>"
            f"<div class='vhead'><span class='vname'>{_esc(name)}</span>"
            f"<span class='{badge_cls}'>{_esc(wlabel)}</span></div>"
            f"<div class='vdesc'>{_esc(desc)}</div>"
            f"<div class='vcalc'>Calc: {_esc(calc)}</div>"
            f"<div class='vthresh'>{ch}</div>"
            "</div>"
        )
    keyline = (
        "<div class='keyline'>"
        "<span class='keyitem'><span class='sqr' style='background:#6aa329'></span>70+ healthy</span>"
        "<span class='keyitem'><span class='sqr' style='background:#e0a64a'></span>40\u201369 watch</span>"
        "<span class='keyitem'><span class='sqr' style='background:#cf6b6b'></span>&lt;40 concern</span>"
        "<span class='keyitem'><span class='sqr' style='background:#b9b7ae'></span>no data</span>"
        "<span class='sep'>\u00b7</span><span class='keynote'>cell size = variable weight</span>"
        "</div>"
    )
    grid = "<div class='vgrid'>" + "".join(cards) + "</div>"
    return (keyline
            + "<div class='vardefs-title'>Iron Condor \u2014 variable definitions &amp; scoring</div>"
            + grid)


def _legend():
    SQ = {
        "green": ("#6aa329", "#3b6d11"),
        "amber": ("#e0a64a", "#ba7517"),
        "red": ("#cf6b6b", "#a32d2d"),
        "gray": ("#b9b7ae", "#7a776d"),
    }

    def chip(ck, word, thresh):
        fill, tx = SQ[ck]
        return (
            f"<span class='tchip'><span class='sqr' style='background:{fill}'></span>"
            f"<span style='color:{tx}'><b>{_esc(word)}</b> {_esc(thresh)}</span></span>"
        )

    defs = [
        ("B/E buffer", "Weight 25%", False,
         "How far the stock can fall before you lose money at expiry. Your real floor — not the strike.",
         "(spot − break-even) ÷ spot × 100  |  Break-even = strike − premium/share",
         [("green", "green", ">15%"), ("amber", "amber", "5–15%"), ("red", "red", "<5% or below b/e")]),
        ("Delta × DTE", "Weight 20%", False,
         "The combined forward signal. Elevated delta is manageable with time remaining; dangerous without it.",
         "current delta (absolute) cross-referenced against DTE remaining.",
         [("green", "green", "delta <0.30 + DTE >21"), ("red", "red", "delta >0.45 + DTE ≤21")]),
        ("Delta", "Weight 15%", False,
         "The market's real-time probability that the option expires in the money and you get assigned.",
         "absolute value of current option delta from IBKR live data or most recent check.",
         [("green", "green", "<0.25"), ("amber", "amber", "0.25–0.40"), ("red", "red", ">0.45")]),
        ("1× stop used", "Weight 15%", False,
         "How much of your maximum acceptable loss has been consumed. 1× stop = premium collected.",
         "unrealized loss ÷ premium collected × 100. Stop triggers at 100%.",
         [("green", "green", "<40%"), ("amber", "amber", "40–70%"), ("red", "red", ">80%")]),
        ("Theta / day", "Weight 10%", False,
         "Daily premium decay in your favor. Answers: is time recovering losses fast enough?",
         "theta × 100/contract. Recovery days = unrealized loss ÷ daily theta.",
         [("green", "green", "recovers in <14d"), ("red", "red", ">30d or no data")]),
        ("DTE", "Weight 10%", False,
         "Days to expiration. A time multiplier that amplifies or forgives every other signal.",
         "calendar days to expiration. Key thresholds: 21d (gamma accelerates), 7d (gamma extreme).",
         [("green", "green", ">30d"), ("amber", "amber", "14–30d"), ("red", "red", "≤7d")]),
        ("Strike buffer", "Weight 5%", False,
         "Distance from spot to the short strike. Context only — less meaningful than b/e buffer.",
         "(spot − strike) ÷ spot × 100. Negative = ITM.",
         [("green", "green", ">10%"), ("amber", "amber", "3–10%"), ("red", "red", "<3% or ITM")]),
        ("Delta drift", "Weight 5%", False,
         "How much assignment probability has shifted since you opened. Direction-of-travel signal.",
         "current delta − entry delta. Requires both entry delta (helm open --confirm) and a recent check.",
         [("green", "green", "<+0.05"), ("amber", "amber", "+0.05–0.20"), ("red", "red", ">+0.20"), ("gray", "gray", "unavailable")]),
        ("IVR", "Context only", True,
         "IV Rank — where today's implied volatility sits in its 52-week range.",
         "(current IV − 52wk low) ÷ (52wk high − 52wk low) × 100. From helm ivr refresh.",
         [("green", "green", ">70 rich premium"), ("red", "red", "<30 compressed")]),
        ("Close now", "Summary row", True,
         "Net dollar result if you buy to close today. Positive = you keep money. Negative = closing costs more than you collected.",
         "unrealized P&L from most recent check. Sold at X, now costs Y to close — net = X−Y per share × 100 × contracts.",
         [("green", "green", "pill net positive"), ("red", "red", "pill net negative (loss exceeds premium)")]),
    ]

    cards = []
    for name, wlabel, is_ctx, desc, calc, chips in defs:
        badge_cls = "wbadge ctx" if is_ctx else "wbadge"
        ch = "".join(chip(*c) for c in chips)
        cards.append(
            "<div class='vcard'>"
            f"<div class='vhead'><span class='vname'>{_esc(name)}</span>"
            f"<span class='{badge_cls}'>{_esc(wlabel)}</span></div>"
            f"<div class='vdesc'>{_esc(desc)}</div>"
            f"<div class='vcalc'>Calc: {_esc(calc)}</div>"
            f"<div class='vthresh'>{ch}</div>"
            "</div>"
        )

    keyline = (
        "<div class='keyline'>"
        "<span class='keyitem'><span class='sqr' style='background:#6aa329'></span>70+ healthy</span>"
        "<span class='keyitem'><span class='sqr' style='background:#e0a64a'></span>40–69 watch</span>"
        "<span class='keyitem'><span class='sqr' style='background:#cf6b6b'></span>&lt;40 concern</span>"
        "<span class='keyitem'><span class='sqr' style='background:#b9b7ae'></span>no data</span>"
        "<span class='sep'>·</span><span class='keynote'>cell size = variable weight</span>"
        "</div>"
    )
    grid = "<div class='vgrid'>" + "".join(cards) + "</div>"
    return keyline + "<div class='vardefs-title'>Cash Secured Put — variable definitions &amp; scoring</div>" + grid




def _bs_put_price_bps(spot, strike, T_days, iv, r=0.043):
    if not (spot and strike and T_days and T_days > 0 and iv and iv > 0): return None
    try:
        T = T_days / 365.0
        d1 = (_ic_math.log(spot/strike) + (r + 0.5*iv**2)*T) / (iv*_ic_math.sqrt(T))
        d2 = d1 - iv*_ic_math.sqrt(T)
        from scipy.stats import norm as _n
        call = spot*_n.cdf(d1) - strike*_ic_math.exp(-r*T)*_n.cdf(d2)
        return max(0.0, call - spot + strike*_ic_math.exp(-r*T))
    except Exception:
        return None

BPS_WEIGHTS = {'be_buffer':0.30,'dte':0.20,'spread_value':0.20,'long_put_delta':0.15,'max_profit_pct':0.10,'stop_used':0.05}

def s_bps_be_buffer(p):
    if p is None: return None
    if p <= -5: return 10
    if p <= 0: return 8
    if p <= 2: return 6
    if p <= 5: return 4
    if p <= 10: return 2
    return 0

def s_bps_spread_value(p):
    if p is None: return None
    if p >= 80: return 10
    if p >= 60: return 8
    if p >= 40: return 6
    if p >= 20: return 4
    if p >= 10: return 2
    return 0

def s_bps_delta(d):
    if d is None: return None
    if d >= 0.70: return 10
    if d >= 0.50: return 8
    if d >= 0.35: return 6
    if d >= 0.25: return 4
    if d >= 0.15: return 2
    return 0

def s_bps_max_profit(p):
    if p is None: return None
    if p >= 80: return 10
    if p >= 60: return 8
    if p >= 40: return 6
    if p >= 20: return 4
    if p >= 0: return 3
    if p >= -50: return 1
    return 0

def gather_bearput(conn, ticker=None):
    sql = """SELECT p.id,p.ticker,p.company_name,p.net_premium,p.total_contracts,p.max_loss,p.max_profit,p.earnings_date,c.spot_price,c.delta,c.theta,c.dte_now,c.pnl_unrealized,c.iv_current,c.checked_at,c.greeks_source,c.data_quality FROM positions p LEFT JOIN checks c ON c.id=(SELECT id FROM checks WHERE position_id=p.id AND data_quality='GOOD' ORDER BY checked_at DESC LIMIT 1) WHERE p.status='OPEN' AND p.strategy='BEAR_PUT_SPREAD'"""
    args = []
    if ticker:
        sql += " AND p.ticker=?"
        args.append(ticker.upper())
    sql += " ORDER BY p.ticker"
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    for r in rows:
        legs = [dict(l) for l in conn.execute("SELECT leg_role,direction,strike,open_price,contracts,expiration FROM legs WHERE position_id=? ORDER BY strike DESC", (r["id"],)).fetchall()]
        lp = next((l for l in legs if l['leg_role']=='LONG_PUT'), None)
        sp = next((l for l in legs if l['leg_role']=='SHORT_PUT'), None)
        r['long_strike'] = lp['strike'] if lp else None
        r['short_strike'] = sp['strike'] if sp else None
        r['expiration'] = lp['expiration'] if lp else (sp['expiration'] if sp else None)
        c = r['total_contracts'] or (lp['contracts'] if lp else 1)
        r['total_contracts'] = c
        d = abs(r['net_premium'] or 0); r['debit_paid'] = d
        r['debit_per_share'] = d/(c*100) if (d and c) else None
        r['breakeven'] = (r['long_strike'] - r['debit_per_share']) if (r['long_strike'] and r['debit_per_share']) else None
        r['spread_width'] = (r['long_strike'] - r['short_strike']) if (r['long_strike'] and r['short_strike']) else None
        r['max_spread_value'] = (r['spread_width']*c*100) if r['spread_width'] else None
        s = r['spot_price']
        r['be_buffer_pct'] = ((s - r['breakeven'])/s*100) if (s and r['breakeven']) else None
        r['in_profit_zone'] = bool(s and r['breakeven'] and s < r['breakeven'])
        iv = (r['iv_current']/100.0) if r['iv_current'] else None
        dte = r['dte_now']
        lpp = _bs_put_price_bps(s, r['long_strike'], dte, iv) if iv else None
        spp = _bs_put_price_bps(s, r['short_strike'], dte, iv) if iv else None
        if lpp is not None and spp is not None:
            r['spread_current_value'] = (lpp - spp)*c*100; r['pnl_calc'] = r['spread_current_value'] - d
        else:
            r['spread_current_value'] = None; r['pnl_calc'] = r['pnl_unrealized']
        r['spread_value_pct'] = (r['spread_current_value']/r['max_spread_value']*100) if (r['spread_current_value'] is not None and r['max_spread_value']) else None
        r['long_put_delta_raw'] = _bs_delta_ic(s, r['long_strike'], dte, iv, option_type='put') if iv else None
        r['abs_long_put_delta'] = abs(r['long_put_delta_raw']) if r['long_put_delta_raw'] is not None else None
        mp = r['max_profit'] or ((r['max_spread_value'] - d) if r['max_spread_value'] else None)
        r['max_profit_display'] = mp
        pnl, r['pnl_source'] = _pnl_pick(r.get('greeks_source'), r['pnl_unrealized'], r['pnl_calc'], d, mp)
        r['pnl_display'] = pnl
        r['max_profit_pct'] = (pnl/mp*100) if (pnl is not None and mp and mp > 0) else None
        r['stop_used_pct'] = (max(0.0, -pnl)/d*100) if (pnl is not None and d > 0) else None
    return rows

def score_bearput(r):
    scores = {
        'be_buffer': s_bps_be_buffer(r['be_buffer_pct']),
        'dte': s_ic_dte(r['dte_now']),
        'spread_value': s_bps_spread_value(r['spread_value_pct']),
        'long_put_delta': s_bps_delta(r['abs_long_put_delta']),
        'max_profit_pct': s_bps_max_profit(r['max_profit_pct']),
        'stop_used': s_stop_used(r['stop_used_pct']),
    }
    num = den = 0.0
    for k, sc in scores.items():
        if sc is None: continue
        w = BPS_WEIGHTS[k]; num += w*sc; den += w
    composite = (num/den*10) if den > 0 else None
    band, band_color = composite_band(composite)
    return {'scores': scores, 'composite': composite, 'band': band, 'band_color': band_color}

def guidance_bearput(r, scored):
    c = scored['composite']; pct = r.get('be_buffer_pct'); dte = r.get('dte_now'); mp = r.get('max_profit_pct')
    if mp is not None and mp >= 80:
        return ('green', 'Approaching maximum profit \u2014 close this position and lock in the gain.')
    if c is None:
        return ('gray', 'Insufficient data \u2014 run a fresh check.')
    if r.get('in_profit_zone'):
        if mp is not None and mp >= 40:
            return ('green', 'Position in profit \u2014 stock below break-even and gaining. Monitor for exit.')
        return ('green', 'Stock below break-even \u2014 position profitable. Let it work or take partial profits.')
    if dte is not None and dte <= 7:
        return ('red', 'Under 7 DTE \u2014 close immediately. Not enough time for the required move.')
    if dte is not None and dte <= 14:
        if pct is not None and pct > 5:
            return ('red', 'Under 14 DTE and still far from break-even \u2014 consider closing to limit loss.')
        return ('amber', 'Under 14 DTE \u2014 monitor closely. Stock needs to move soon.')
    if c >= 70:
        return ('green', 'On track \u2014 stock moving toward break-even. Let theta work.')
    if c >= 40:
        return ('amber', 'Watch \u2014 stock needs to continue lower. Monitor for time decay erosion.')
    return ('red', 'At risk \u2014 significant move needed with limited time. Evaluate cutting loss.')

def _summary_facts_bps(r):
    spot = r['spot_price']; pnl = r['pnl_display']; be = r['breakeven']; ls = r['long_strike']; ss = r['short_strike']
    spot_str = ('%.2f' % spot) if spot is not None else '\u2014'
    be_str = ('$%.2f' % be) if be is not None else '\u2014'
    pct = r.get('be_buffer_pct')
    if r.get('in_profit_zone'): spot_cls = 'spot-g'
    elif pct is not None and pct <= 7: spot_cls = 'spot-a'
    else: spot_cls = 'spot-r'
    pill_cls = 'pill-g' if (pnl is not None and pnl >= 0) else 'pill-r'
    pill = ("<span class='pill " + pill_cls + " mono'>" + _money(pnl) + "</span>") if pnl is not None else ''
    _ps = r.get('pnl_source')
    src = ("<span class='pill-src" + (" pill-src-est" if _ps in ('BS est','recorded','n/a') else "") + "'>" + _ps + "</span>") if (pnl is not None and _ps) else ''
    itm = "<span class='badge-itm'>PROFITABLE</span>" if r.get('in_profit_zone') else ''
    strikes = ('%.0f\u2013%.0f' % (ss, ls)) if (ls and ss) else '\u2014'
    return ("<div class='facts'>"
        + "<span class='fact'><span class='lbl'>Spot</span><b class='mono " + spot_cls + "'>" + spot_str + "</b></span>"
        + "<span class='fact'><span class='lbl'>Strikes</span><b class='mono'>" + strikes + "</b></span>"
        + "<span class='fact'><span class='lbl'>B/E</span><b class='mono'>" + be_str + "</b></span>"
        + "<span class='fact'><span class='lbl'>DTE</span><b class='mono'>" + str(r['dte_now']) + "</b></span>"
        + pill + src + itm + "</div>")

def _render_map_bps(r, sc):
    s = sc['scores']
    pct = r['be_buffer_pct']
    pct_str = ('%+.1f%%' % pct) if pct is not None else '\u2014'
    pct_sub = 'below b/e \u2014 profitable' if r.get('in_profit_zone') else (('b/e at $%.2f' % r['breakeven']) if r['breakeven'] else 'no data')
    dte = r['dte_now']; dte_val = (str(dte) + 'd') if dte is not None else '\u2014'
    row1 = ("<div class='maprow' style='grid-template-columns:2.5fr 1.5fr;'>"
        + _cell('B/E buffer', pct_str, s['be_buffer'], pct_sub)
        + _cell('DTE', dte_val, s['dte'], 'days remaining') + "</div>")
    sv = r['spread_value_pct']; sv_val = ('%.0f%%' % sv) if sv is not None else '\u2014'
    sv_sub = ('$%.0f of $%.0f max' % (r['spread_current_value'], r['max_spread_value'])) if (r['spread_current_value'] and r['max_spread_value']) else 'BS approx'
    ld = r['abs_long_put_delta']; ld_val = ('%.3f' % ld) if ld is not None else '\u2014'
    mp = r['max_profit_pct']; mp_val = ('%.0f%%' % mp) if mp is not None else '\u2014'
    row2 = ("<div class='maprow' style='grid-template-columns:1fr 1fr 1fr;'>"
        + _cell('Spread value', sv_val, s['spread_value'], sv_sub, 'sm')
        + _cell('Long put \u03b4', ld_val, s['long_put_delta'], 'BS approx', 'sm')
        + _cell('% max profit', mp_val, s['max_profit_pct'], 'of max', 'sm') + "</div>")
    su = r['stop_used_pct']; su_val = ('%.0f%%' % su) if su is not None else '\u2014'
    gc = CELL['gray']; gs = 'background:' + gc['bg'] + ';border-color:' + gc['bd'] + ';color:' + gc['tx'] + ';'
    debit_cell = ("<div class='cell sm' style='" + gs + "'><div class='clabel'>Net debit</div>"
        + "<div class='cellfoot'><span class='cval mono' style='color:" + gc['vl'] + ";'>" + _money(r['debit_paid']) + "</span></div>"
        + "<div class='cellfoot'><span class='csub'>paid to enter</span><span class='schip'>ref</span></div></div>")
    mp_ref = r['max_profit_display']; ss = r['short_strike']
    mp_money = _money(mp_ref) if mp_ref else '\u2014'
    ss_str = ('%.0f' % ss) if ss is not None else '\u2014'
    mpp_cell = ("<div class='cell sm' style='" + gs + "'><div class='clabel'>Max profit</div>"
        + "<div class='cellfoot'><span class='cval mono' style='color:" + gc['vl'] + ";'>" + mp_money + "</span></div>"
        + "<div class='cellfoot'><span class='csub'>if spot &lt; " + ss_str + "</span><span class='schip'>ref</span></div></div>")
    row3 = ("<div class='maprow' style='grid-template-columns:1fr 1fr 1fr;'>"
        + _cell('1\u00d7 stop', su_val, s['stop_used'], 'of debit paid', 'sm')
        + debit_cell + mpp_cell + "</div>")
    return "<div class='map'>" + row1 + row2 + row3 + "</div>"

def _card_bps(r, sc, link=True):
    g_color, g_text = guidance_bearput(r, sc)
    co = _esc(r['company_name'] or '')
    head = ("<div class='hrow'><span class='tk'>" + _esc(r['ticker']) + "</span><span class='co'>" + co + "</span><span class='spacer'></span>" + _comp_badge(sc) + "</div>")
    body = _summary_facts_bps(r) + _render_map_bps(r, sc)
    guide = "<div class='guide g-" + g_color + "'>" + _esc(g_text) + "</div>"
    inner = head + body + guide
    if link:
        return "<a class='card' href='/health?ticker=" + _esc(r['ticker']) + "'>" + inner + "</a>"
    return "<div class='card'>" + inner + "</div>"

def _legend_bps():
    SQ = {'green':('#6aa329','#3b6d11'),'amber':('#e0a64a','#ba7517'),'red':('#cf6b6b','#a32d2d'),'gray':('#b9b7ae','#7a776d')}
    def chip(ck, word, thresh):
        fill, tx = SQ[ck]
        return ("<span class='tchip'><span class='sqr' style='background:" + fill + "'></span><span style='color:" + tx + "'><b>" + _esc(word) + "</b> " + _esc(thresh) + "</span></span>")
    defs = [
        ('B/E buffer','Weight 30%',False,'Distance from spot to break-even as % of spot. Negative = stock fell below break-even (profitable).','(spot - breakeven) / spot * 100.',[('green','green','<=0%'),('amber','amber','0-5%'),('red','red','>5%')]),
        ('DTE','Weight 20%',False,'Days to expiration. Time decay works against debit spreads.','Calendar days to expiry.',[('green','green','>30d'),('amber','amber','7-21d'),('red','red','<=7d')]),
        ('Spread value','Weight 20%',False,'Current spread value as % of max. Rises as stock moves through the strikes.','BS approx vs max spread value.',[('green','green','>=60%'),('amber','amber','20-60%'),('red','red','<20%')]),
        ('Long put delta','Weight 15%',False,'Absolute delta of the long put. Rising = put gaining ITM probability.','Black-Scholes from spot, long strike, DTE, IV.',[('green','green','>=0.50'),('amber','amber','0.25-0.50'),('red','red','<0.25')]),
        ('% max profit','Weight 10%',False,'P&L as % of max profit. At 80%+ consider closing.','pnl / max_profit * 100.',[('green','green','>=80%'),('amber','amber','20-80%'),('red','red','<0%')]),
        ('1x stop','Weight 5%',False,'Loss as % of debit paid. Hard stop 50-60%.','max(0,-pnl) / debit_paid * 100.',[('green','green','0%'),('amber','amber','<40%'),('red','red','>=60%')]),
        ('Net debit','Reference',True,'Premium paid to enter. This is your maximum possible loss.','abs(net_premium).',[]),
        ('Max profit','Reference',True,'(spread_width - net_debit) * contracts * 100. Achieved if spot < short strike at expiry.','max_profit from position record.',[]),
    ]
    cards = []
    for name, wlabel, is_ctx, desc, calc, chips in defs:
        badge_cls = 'wbadge ctx' if is_ctx else 'wbadge'
        ch = ''.join(chip(*c) for c in chips)
        cards.append("<div class='vcard'><div class='vhead'><span class='vname'>" + _esc(name) + "</span><span class='" + badge_cls + "'>" + _esc(wlabel) + "</span></div>" + "<div class='vdesc'>" + _esc(desc) + "</div>" + "<div class='vcalc'>Calc: " + _esc(calc) + "</div>" + "<div class='vthresh'>" + ch + "</div></div>")
    keyline = ("<div class='keyline'>"
        + "<span class='keyitem'><span class='sqr' style='background:#6aa329'></span>70+ healthy</span>"
        + "<span class='keyitem'><span class='sqr' style='background:#e0a64a'></span>40-69 watch</span>"
        + "<span class='keyitem'><span class='sqr' style='background:#cf6b6b'></span>&lt;40 concern</span>"
        + "<span class='keyitem'><span class='sqr' style='background:#b9b7ae'></span>no data</span>"
        + "<span class='sep'>\u00b7</span><span class='keynote'>cell size = variable weight</span></div>")
    return keyline + "<div class='vardefs-title'>Bear Put Spread \u2014 variable definitions &amp; scoring</div>" + "<div class='vgrid'>" + ''.join(cards) + "</div>"
