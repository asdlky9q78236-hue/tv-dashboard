#!/usr/bin/env python3
"""
Build a single mobile-first control dashboard (dashboard/index.html) that
aggregates the latest Scanner A/B/C outputs and strategy performance.

Run:  python build_dashboard.py
Serve locally:  python -m http.server 8000 --directory dashboard
Then open http://<this-pc-ip>:8000 on your phone (same Wi-Fi), or expose it
remotely via a tunnel (see README) for access when away from home.
"""
from __future__ import annotations

import json
import glob
import html
from pathlib import Path

import common as C
import compute_perf as P
import config as CFG

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
DASH = ROOT / "dashboard"


def _latest(prefix: str) -> dict | None:
    files = sorted(glob.glob(str(OUT / f"{prefix}_*.json")))
    if not files:
        return None
    try:
        return json.loads(Path(files[-1]).read_text(encoding="utf-8"))
    except Exception:
        return None


def _session(now) -> tuple[str, str]:
    t = now.time()
    import datetime as dt
    if now.weekday() >= 5:
        return "Weekend", "secondary"
    if dt.time(4, 0) <= t < dt.time(9, 30):
        return "Premarket", "info"
    if dt.time(9, 30) <= t < dt.time(16, 0):
        return "Open", "success"
    if dt.time(16, 0) <= t < dt.time(20, 0):
        return "After-hours", "warning"
    return "Closed", "secondary"


def card(title, body, extra=""):
    return (f'<div class="col-12 col-lg-6"><div class="card bg-dark border-secondary h-100">'
            f'<div class="card-body"><h6 class="text-info mb-3">{title}{extra}</h6>'
            f'{body}</div></div></div>')


# --------------------------------------------------------------------------
# Plain-language interpretation helpers (so a non-pro can read the data)
# --------------------------------------------------------------------------
def _rvol_words(rv) -> str:
    if not rv:
        return "volume onbekend"
    if rv >= 2:
        return f"ongewoon druk ({rv}× normaal)"
    if rv >= 1.2:
        return f"drukker dan normaal ({rv}×)"
    if rv >= 0.8:
        return f"rond normaal volume ({rv}×)"
    return f"rustige handel ({rv}×)"


def _gap_read(h) -> str:
    g = h["gap_pct"]; rv = h.get("rvol", 0)
    richting = "omhoog" if g >= 0 else "omlaag"
    grootte = "Grote" if abs(g) >= 7 else ("Flinke" if abs(g) >= 4 else "Lichte")
    if rv and rv >= 2:
        extra = " — sterke, meestal nieuwsgedreven beweging"
    elif rv and rv < 1:
        extra = " — zonder volume minder overtuigend"
    else:
        extra = ""
    return f"{grootte} sprong {richting}, {_rvol_words(rv)}{extra}."


_TJL_LABELS = {
    "D1_above_daily200": "boven 200-daags gemiddelde (opgaande trend)",
    "D2_above_prior_high": "boven gisteren's hoogste koers",
    "I1_above_intraday200": "boven de intraday-trendlijn",
    "I2_above_pmh": "boven de premarket-piek",
    "I3_at_hod": "op of nabij de dagtop",
    "I4_rvol_ok": f"volume ≥ {CFG.SCANNER_B_RVOL_MIN}× normaal",
}


def _tjl_status(r):
    """(label, kleur, samenvatting, [ontbrekende voorwaarden]) in gewone taal."""
    if not r.get("ok"):
        return ("Datafout", "secondary", "Geen data beschikbaar.", [])
    missing = [_TJL_LABELS[k] for k, v in r["conditions"].items() if not v]
    if r["PASS"]:
        return ("✅ Setup actief", "success",
                "Alle voorwaarden voldaan — momentum-uitbraak nú.", missing)
    if r["daily_pass"]:
        return ("Trend OK · wacht op uitbraak", "warning",
                "In opgaande trend, maar de intraday-uitbraak is nog niet bevestigd.", missing)
    return ("Wordt gevolgd", "secondary",
            "Nog geen setup — voldoet niet aan de trend/uitbraak-voorwaarden.", missing)


def scanner_a_html(rep):
    intro = ("<div class='small text-muted mb-2'>Aandelen die <b>vóór de opening</b> al flink "
             "bewegen t.o.v. de slotkoers van gisteren. Grote sprong + hoog volume = ongewone "
             "activiteit, vaak door nieuws.</div>")
    if not rep or not rep.get("hits"):
        return card("📈 Gap screener", intro + '<div class="text-muted">Geen kandidaten nu.</div>')
    rows = ""
    for h in rep["hits"]:
        sign = "+" if h["gap_pct"] >= 0 else ""
        col = "success" if h["gap_pct"] >= 0 else "danger"
        mc_b = f"${h['market_cap_b']}B" if h.get("market_cap_b") else "-"
        cap = h.get("cap_class")
        mc = f"{mc_b} <span class='small text-muted'>{cap}</span>" if cap else mc_b
        cat = (h["news"][0]["title"] if h.get("news") else "")[:60]
        rows += (f"<tr><td><b>{html.escape(h['symbol'])}</b></td>"
                 f"<td class='text-{col}'>{sign}{h['gap_pct']}%</td>"
                 f"<td>{h['rvol']}x</td><td>{mc}</td>"
                 f"<td class='small text-muted'>{html.escape(cat) or '—'}</td></tr>"
                 f"<tr class='small'><td></td>"
                 f"<td colspan='4' class='text-info pb-2'>↳ {_gap_read(h)}</td></tr>")
    body = (intro +
            f"<div class='small text-muted mb-2'>{rep['count']} kandidaten · "
            f"gap≥{rep['filters'].get('gap_min_pct')}%</div>"
            f"<div class='table-responsive'><table class='table table-dark table-sm align-middle'>"
            f"<thead><tr><th>Sym</th><th>Gap</th><th>RVol</th><th>Grootte</th><th>Reden/nieuws</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
            "<div class='small text-muted mt-1'><b>Gap</b> = verschil met gisteren · "
            "<b>RVol</b> = volume vs normaal (&gt;1 = drukker) · "
            "<b>Grootte</b> = marktwaarde bedrijf</div>")
    return card("📈 Gap screener", body)


def scanner_b_html(rep):
    intro = ("<div class='small text-muted mb-2'>Een <b>momentum-strategie</b>: zoekt een aandeel "
             "in een opgaande trend dat uitbreekt naar nieuwe highs op sterk volume. Vaste "
             "watchlist; een 'setup' ontstaat pas als <b>álle</b> voorwaarden kloppen.</div>")
    if not rep:
        return card("🎯 Momentum-setups (TJL)", intro + '<div class="text-muted">Geen data.</div>')
    items = ""
    for r in rep.get("results", []):
        label, col, summ, missing = _tjl_status(r)
        sym = html.escape(r.get("symbol", "?"))
        price = f"${r['last']}" if r.get("ok") else ""
        miss = ""
        if missing and r.get("ok") and not r["PASS"]:
            miss = ("<div class='small text-muted'>Nog nodig: " + ", ".join(missing) + "</div>")
        items += (f"<div class='border-top border-secondary py-2'>"
                  f"<div class='d-flex justify-content-between align-items-center'>"
                  f"<span><b>{sym}</b> <span class='small text-muted'>{price}</span></span>"
                  f"<span class='badge bg-{col}'>{label}</span></div>"
                  f"<div class='small'>{summ}</div>{miss}</div>")
    return card("🎯 Momentum-setups (TJL)", intro + items)


def how_to_read_html():
    body = (
        "<p class='small mb-2'>Dit dashboard signaleert <b>kansen</b> die het automatisch "
        "scant — geen koopadviezen. Het is gebouwd rond de strategieën van "
        "<b>HumbledTrader</b> (uit haar eigen video's):</p>"
        "<p class='small mb-1'><b>📈 Haar strategieën in het kort</b></p>"
        "<ul class='small mb-2'>"
        "<li><b>Gap &amp; Go (long)</b> — een small/mid-cap die op vers nieuws gapt. Ze koopt "
        "<b>de pullback naar VWAP die houdt</b> (de dip), <b>niet</b> de breakout. Instaptypes: "
        "break van de premarket-piek op een terugtest, 1e/2e pullback (bull-flag bij VWAP), "
        "red-to-green (ochtenddip die terugkeert naar groen).</li>"
        "<li><b>Fade (short)</b> — een grote gap die <b>faalt en VWAP verliest</b> "
        "('fails VWAP → wegwezen'). Het tegenovergestelde spel op dezelfde gapper.</li>"
        "<li><b>Vuistregels</b> — alleen de eerste ~1–2 uur na de opening; max 1–2 setups/dag; "
        "vermijd <b>overextended</b> (>15% premarket gelopen); stop ~4%, neem winst in stukjes, "
        "'breakout or bail' (loopt het niet door → eruit).</li></ul>"
        "<p class='small mb-1'><b>Hoe de kaarten daarop aansluiten:</b></p>"
        "<ul class='small mb-2'>"
        "<li><b>Gap screener</b> — wat gapt er vandaag op nieuws (de jachtgrond), met cap-klasse.</li>"
        "<li><b>VWAP-watch</b> — vertaalt dat live: <b>VWAP-pullback</b> = haar instap (koop de dip); "
        "<b>Long (extended)</b> = op de top (najagen = risico); <b>fade-short</b> = gap die VWAP "
        "verloor. Met SPY-marktcontext (rugwind/tegenwind).</li>"
        "<li><b>Momentum-setups (TJL)</b> — vaste tickers; groen 'setup actief' = alle "
        "voorwaarden kloppen nú.</li></ul>"
        "<p class='small mb-1'><b>Wat traders hier doorgaans mee doen</b> (educatief):</p>"
        "<ul class='small mb-2'>"
        "<li>De lijst als <b>watchlist</b> gebruiken en een koersalert zetten i.p.v. meteen handelen.</li>"
        "<li>Op <b>bevestiging</b> wachten — een gap die al ver gelopen is, niet achternajagen.</li>"
        "<li>Eerst <b>op papier oefenen</b> (paper trading) om het ritme te leren zonder risico.</li>"
        "<li>Vooraf bepalen <b>hoeveel je bereid bent te verliezen</b> per idee.</li></ul>"
        "<div class='small text-warning'>⚠️ Educatief, geen financieel advies. "
        "Handelen brengt risico op verlies met zich mee.</div>"
    )
    return ('<div class="col-12"><div class="card bg-dark border-secondary">'
            '<div class="card-body"><h6 class="text-info mb-3">📚 Hoe lees ik dit?</h6>'
            f'{body}</div></div></div>')


def scanner_c_html(rep):
    if not rep:
        return card("🪙 Crypto pairs", '<div class="text-muted">No data.</div>')
    rows = ""
    for r in rep.get("ranked", [])[:8]:
        live = "🔥" if r.get("live_setup") else ""
        rows += (f"<tr><td><b>{html.escape(r['symbol'])}</b> {live}</td>"
                 f"<td>{r['per_month']}/mo</td><td>{r['win_rate']}%</td>"
                 f"<td>{r['avg_fwd']:+}%</td></tr>")
    live = ", ".join(rep.get("live_setups", [])) or "none"
    body = (f"<div class='small text-muted mb-2'>live: {live}</div>"
            f"<table class='table table-dark table-sm'>"
            f"<thead><tr><th>Pair</th><th>Freq</th><th>Win</th><th>Edge</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>")
    return card("🪙 Crypto pairs", body)


def scanner_d_html(rep):
    intro = ("<div class='small text-muted mb-2'>Live check op de dag-gappers t.o.v. <b>VWAP</b> "
             "(de gele lijn uit de video). <b>VWAP-pullback</b> = teruggezakt naar VWAP en houdt "
             "het — dít is HumbledTrader's eigenlijke instap (koop de dip, niet de top). "
             "<b>Long (extended)</b> = op de dagtop, momentum maar najagen is risico. "
             "<b>Fade-short</b> = grote gap die VWAP verloor. Signalen, geen koopadvies.</div>")
    if not rep:
        return card("🌀 VWAP-watch (live movers)",
                    intro + '<div class="text-muted">Geen data.</div>')
    m = rep.get("market", {})
    banner = ""
    if m.get("pct") is not None:
        tone_col = {"rugwind": "success", "tegenwind": "danger"}.get(m.get("tone"), "secondary")
        banner = (f"<div class='mb-2'><span class='badge bg-{tone_col}'>Markt: SPY "
                  f"{m['pct']:+}% · {m.get('tone', '?')}</span> "
                  f"<span class='small text-muted'>(handel je mét of tegen de markt in?)</span></div>")
    kinds = {"long_pullback": ("VWAP-pullback", "success"),
             "long_extended": ("Long (extended)", "info"),
             "fade_short_watch": ("Fade-short", "danger"),
             "neutral": ("Neutraal", "secondary"),
             "premarket": ("Wacht op open", "secondary")}
    items = ""
    for r in rep.get("results", []):
        if not r.get("ok"):
            continue
        label, col = kinds.get(r["kind"], ("?", "secondary"))
        sym = html.escape(r["symbol"])
        cap = r.get("cap_class")
        cap_badge = f" <span class='badge bg-dark border border-secondary text-muted'>{cap}-cap</span>" if cap else ""
        vw = f" · VWAP ${r['vwap']}" if r.get("vwap") else ""
        items += (f"<div class='border-top border-secondary py-2'>"
                  f"<div class='d-flex justify-content-between align-items-center'>"
                  f"<span><b>{sym}</b>{cap_badge} <span class='small text-muted'>${r.get('last')}{vw}</span></span>"
                  f"<span class='badge bg-{col}'>{label}</span></div>"
                  f"<div class='small text-muted'>{', '.join(r.get('why', []))}</div></div>")
    if not items:
        items = "<div class='text-muted small'>Geen movers om te tonen.</div>"
    return card("🌀 VWAP-watch (live movers)", intro + banner + items)


ANALYSES_DIR = ROOT.parent / "analyses"


def analyses_html():
    """Trading diary: each flagged setup as an entry (plan) + after-the-fact review."""
    try:
        log = json.loads((ANALYSES_DIR / "log.json").read_text(encoding="utf-8"))
    except Exception:
        log = []
    if not log:
        return ""
    intro = ("<div class='small text-muted mb-2'>Elke gesignaleerde setup als "
             "<b>entry</b> (plan) + <b>achteraf-analyse</b> (doel/stop geraakt, les). "
             "Klik een afbeelding om te vergroten.</div>")
    items = ""
    for a in log[:12]:
        img = html.escape(a.get("image", ""))
        g = a.get("grade")
        if g == "A" and a.get("crowned"):
            gbadge = " <span class='badge bg-warning text-dark'>⭐ A-SETUP</span>"
        elif g == "A":
            gbadge = " <span class='badge bg-warning text-dark'>A</span>"
        elif g == "B":
            gbadge = " <span class='badge bg-secondary'>B</span>"
        elif g == "C":
            gbadge = " <span class='badge bg-dark border border-secondary text-muted'>C</span>"
        else:
            gbadge = ""
        cvbadge = {"HIGH": " <span class='badge bg-success'>🟢 HIGH</span>",
                   "MEDIUM": " <span class='badge bg-warning text-dark'>🟡 MED</span>",
                   "LOW": " <span class='badge bg-danger'>🔴 LOW</span>"}.get(a.get("conviction"), "")
        head = ("<div class='d-flex justify-content-between align-items-center mb-1'>"
                f"<span><b>{html.escape(a.get('symbol',''))}</b> "
                f"<span class='badge bg-secondary'>{html.escape(a.get('label',''))}</span>{gbadge}{cvbadge}</span>"
                f"<span class='small text-muted'>{html.escape(a.get('et_time',''))}</span></div>")
        plan = ""
        if a.get("entry"):
            bits = [f"entry ${a['entry']}"]
            if a.get("stop"):
                bits.append(f"stop ${a['stop']}")
            if a.get("target"):
                bits.append(f"doel ${a['target']}")
            if a.get("rr"):
                bits.append(f"R:R 1:{a['rr']}")
            plan = f"<div class='small'>📋 <b>Plan:</b> {' · '.join(bits)}</div>"
        r = a.get("review")
        if r:
            rev = (f"<div class='small mt-1'>🔎 <b>Achteraf:</b> {html.escape(r.get('outcome',''))} "
                   f"<span class='text-muted'>— {html.escape(r.get('lesson',''))}</span></div>")
        elif a.get("entry"):
            rev = "<div class='small text-muted mt-1'>🔎 Achteraf-analyse volgt (~45 min)…</div>"
        else:
            rev = ""
        img_html = (f"<a href='{img}' target='_blank'><img src='{img}' loading='lazy' "
                    "class='img-fluid rounded border border-secondary mt-1'></a>") if img else ""
        items += f"<div class='border-top border-secondary py-2'>{head}{plan}{rev}{img_html}</div>"
    return ('<div class="col-12"><div class="card bg-dark border-secondary">'
            '<div class="card-body"><h6 class="text-info mb-3">📓 Trading diary</h6>'
            f'{intro}{items}</div></div></div>')


def backtest_html():
    """Edge-tracker: win-rate + expectancy (R) by grade/conviction, over the
    append-only history (never truncated). Proves whether the strategy has an edge."""
    recs = C.read_backtest_log()
    if not recs:
        return ""
    real = [r for r in recs if not r.get("shadow")]
    shadow = [r for r in recs if r.get("shadow")]
    st = C.backtest_stats(real)
    ov = st["overall"]

    def row(label, s):
        wr = f"{s['win_rate']}%" if s["win_rate"] is not None else "—"
        exp = f"{s['expectancy_r']:+}R" if s["expectancy_r"] is not None else "—"
        return (f"<tr><td>{html.escape(str(label))}</td><td>{s['n']}</td>"
                f"<td>{s['wins']}/{s['losses']}/{s['scratch']}</td><td>{wr}</td>"
                f"<td>{exp}</td><td>{s['avg_mfe']}%</td></tr>")

    def table(title, d):
        rows = "".join(row(k, v) for k, v in d.items() if v["n"])
        if not rows:
            return ""
        return (f"<div class='small text-muted mt-2 mb-1'>{title}</div>"
                "<table class='table table-sm table-dark table-borderless small mb-0'>"
                "<thead><tr><th></th><th>n</th><th>W/L/–</th><th>win%</th><th>exp</th>"
                "<th>gem.MFE</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>")

    if ov["n"]:
        wr = ov["win_rate"] if ov["win_rate"] is not None else "—"
        exp = ov["expectancy_r"] if ov["expectancy_r"] is not None else "—"
        ov_line = (f"📈 <b>{ov['n']}</b> afgeronde setups · <b>{wr}%</b> win · "
                   f"expectancy <b>{exp}R</b>")
    else:
        ov_line = "📈 nog geen afgeronde trades"
    intro = ("<div class='small text-muted mb-2'>Bewijst de edge: win-rate + expectancy (R) "
             "per grade/conviction over álle setups. W/L/– = wins/losses/geen-hit.</div>")
    kind_lbl = {"long_pullback": "🟢 Long-pullback", "fade_short_watch": "🔴 Fade-short",
                "long_extended": "🔵 Extended"}
    kind_tbl = {kind_lbl.get(k, k): v for k, v in st["kind"].items()}
    shadow_line = ""
    if shadow:
        ss = C.backtest_stats(shadow)["overall"]
        wr = ss["win_rate"] if ss["win_rate"] is not None else "—"
        ex = ss["expectancy_r"] if ss["expectancy_r"] is not None else "—"
        shadow_line = ("<div class='small text-muted mt-2 border-top border-secondary pt-2'>"
                       "🔬 <b>Fades (data-only, niet getraded)</b> — valideert de fade-ban: "
                       f"n={ss['n']} · win {wr}% · exp {ex}R · "
                       f"W/L/– {ss['wins']}/{ss['losses']}/{ss['scratch']}</div>")
    body = (f"<div class='mb-1'>{ov_line}</div>{intro}"
            + table("Per type (long vs fade)", kind_tbl)
            + table("Per grade", st["grade"])
            + table("Per conviction", st["conviction"])
            + table("⭐ A-setup vs overig", st["crowned"])
            + shadow_line)
    return ('<div class="col-12"><div class="card bg-dark border-secondary">'
            '<div class="card-body"><h6 class="text-info mb-2">📈 Backtest / edge-tracker</h6>'
            f'{body}</div></div></div>')


_WB_ORDER = [("long_pullback", "🟢 Pullback", "success"),
             ("fade_short_watch", "🔴 Fade-short", "danger"),
             ("long_extended", "🔵 Extended", "info"),
             ("neutral", "⚪ Neutraal", "secondary"),
             ("premarket", "⏳ Pre-open", "secondary")]


def watchboard_html():
    """Compact one-glance list of ALL current setups (from scanner_d)."""
    d = _latest("scanner_d")
    if not d:
        return ""
    groups = {}
    for r in d.get("results", []):
        if not r.get("ok"):
            continue
        rel = "&gt;VWAP" if r.get("above_vwap") else "&lt;VWAP"
        cap = r.get("cap_class")
        kind = r.get("kind", "neutral")
        gr = C.wb_grade(r) if kind in ("long_pullback", "fade_short_watch") else None
        if gr == "C":                      # overtrading brake: hide C-grade setups
            continue
        gb = {"A": "<span class='badge bg-warning text-dark'>A</span> ",
              "B": "<span class='badge bg-secondary'>B</span> ",
              "C": "<span class='badge bg-dark border border-secondary text-muted'>C</span> "}.get(gr, "")
        tag = f"{gb}{html.escape(r['symbol'])} <span class='text-muted small'>{rel}"
        tag += f" · {cap}" if cap else ""
        tag += "</span>"
        groups.setdefault(kind, []).append(tag)
    if not groups:
        return ""
    m = d.get("market", {})
    banner = ""
    if m.get("pct") is not None:
        tone_col = {"rugwind": "success", "tegenwind": "danger"}.get(m.get("tone"), "secondary")
        banner = (f"<span class='badge bg-{tone_col}'>Markt: SPY {m['pct']:+}% · "
                  f"{m.get('tone', '?')}</span>")
    rows = ""
    for kind, label, col in _WB_ORDER:
        if groups.get(kind):
            rows += (f"<div class='mb-1'><span class='badge bg-{col}'>{label}</span> "
                     f"{' · '.join(groups[kind])}</div>")
    body = (f"<div class='small text-muted mb-2'>Alle movers in één oogopslag. {banner}</div>{rows}")
    return ('<div class="col-12"><div class="card bg-dark border-info">'
            '<div class="card-body"><h6 class="text-info mb-3">🖥️ Watchboard</h6>'
            f'{body}</div></div></div>')


def perf_html():
    agg = P.aggregate(P.pair_trades(P.load_trades()))
    pf = agg["profit_factor"]; pf_s = "∞" if pf is None else pf
    col = "success" if agg["gross_pnl"] >= 0 else "danger"
    body = (f"<div class='row text-center'>"
            f"<div class='col-4'><div class='text-muted small'>Net P&L</div>"
            f"<div class='h5 text-{col}'>${agg['gross_pnl']:+,.0f}</div></div>"
            f"<div class='col-4'><div class='text-muted small'>Win rate</div>"
            f"<div class='h5'>{agg['win_rate']}%</div></div>"
            f"<div class='col-4'><div class='text-muted small'>Profit factor</div>"
            f"<div class='h5'>{pf_s}</div></div></div>"
            f"<div class='small text-muted mt-2'>{agg['total']} trades · "
            f"{agg['wins']}W / {agg['losses']}L</div>")
    return card("💰 Performance", body)


def build(public: bool = False, out_path: Path | None = None) -> Path:
    now = C.et_now()
    sess, sess_col = _session(now)
    a, b, c = _latest("scanner_a"), _latest("scanner_b"), _latest("scanner_c")
    d = _latest("scanner_d")

    parts = [watchboard_html(), how_to_read_html(),
             scanner_a_html(a), scanner_d_html(d), analyses_html(), backtest_html(),
             scanner_b_html(b), scanner_c_html(c)]
    if not public:                       # keep P&L private off the public URL
        parts.append(perf_html())
    cards = "".join(parts)
    htmldoc = f"""<!doctype html><html lang="en" data-bs-theme="dark"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="120">
<title>TV Control</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head><body class="bg-black text-light">
<div class="container-fluid py-3" style="max-width:1100px">
  <div class="d-flex justify-content-between align-items-center mb-3">
    <h4 class="mb-0">📊 Trading Control</h4>
    <span class="badge bg-{sess_col}">{sess}</span>
  </div>
  <div class="small text-muted mb-3">Updated {now:%Y-%m-%d %H:%M} ET · auto-refresh 2&nbsp;min</div>
  <div class="row g-3">{cards}</div>
  <div class="text-center text-muted small mt-4">TV Scanner pipeline · refresh by re-running build_dashboard.py</div>
</div></body></html>"""
    path = out_path or (DASH / "index.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(htmldoc, encoding="utf-8")
    return path


def to_telegram() -> str:
    now = C.et_now()
    sess, _ = _session(now)
    a, b, c = _latest("scanner_a"), _latest("scanner_b"), _latest("scanner_c")
    lines = [f"<b>📊 Trading Control</b> · {sess}",
             f"<i>{now:%Y-%m-%d %H:%M} ET</i>"]
    if a and a.get("hits"):
        top = a["hits"][:6]
        lines.append("\n<b>Gap screener</b> <i>(beweging vóór open vs gisteren)</i>:")
        for h in top:
            s = "+" if h["gap_pct"] >= 0 else ""
            lines.append(f"  {h['symbol']} {s}{h['gap_pct']}%  rvol {h['rvol']}x")
    if b and b.get("hits"):
        lines.append(f"\n<b>Momentum-setups (TJL):</b> {', '.join(b['hits'])} "
                     f"<i>(uitbraak in optrend)</i>")
    d = _latest("scanner_d")
    if d:
        m = d.get("market", {})
        if m.get("pct") is not None:
            lines.append(f"\n<b>Markt:</b> SPY {m['pct']:+}% ({m.get('tone', '?')})")
        if d.get("long_pullback"):
            lines.append(f"<b>VWAP-pullback:</b> {', '.join(d['long_pullback'])} "
                         f"<i>(dip naar VWAP die houdt — haar instap)</i>")
        if d.get("long_extended"):
            lines.append(f"<b>Long (extended):</b> {', '.join(d['long_extended'])} "
                         f"<i>(op dagtop — niet najagen)</i>")
        if d.get("short_watch"):
            lines.append(f"<b>Fade-short:</b> {', '.join(d['short_watch'])} "
                         f"<i>(gap faalde onder VWAP)</i>")
    if c and c.get("live_setups"):
        lines.append(f"<b>Crypto live:</b> {', '.join(c['live_setups'])}")
    agg = P.aggregate(P.pair_trades(P.load_trades()))
    if agg["total"]:
        pf = agg["profit_factor"]
        lines.append(f"\n<b>P&L:</b> ${agg['gross_pnl']:+,.0f} · "
                     f"win {agg['win_rate']}% · PF {'∞' if pf is None else pf}")
    lines.append("\n<i>ℹ️ Signalen, geen financieel advies. Volledige uitleg op het dashboard.</i>")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Telegram alerting with intraday dedup
# --------------------------------------------------------------------------
# Premarket → always send the morning briefing. Intraday (regular session) →
# only alert when a NEW TJL setup appears versus what we already alerted today,
# so the 30-min cadence doesn't keep repeating the same setups. State is a small
# JSON committed next to index.html so it survives between cloud runs.
STATE_PATH = ROOT.parent / "tg_state.json"


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[telegram] could not write state: {e}")


_SETUP_WORDS = {"tjl": "TJL", "pullback": "VWAP-pullback",
                "ext": "long (extended)", "short": "fade-short"}


def _active_setups() -> list[str]:
    """Setup keys across scanners, e.g. 'AMD:tjl', 'SHOP:short', 'PLUG:pullback'."""
    keys = []
    b = _latest("scanner_b")
    if b:
        keys += [f"{s}:tjl" for s in b.get("hits", [])]
    d = _latest("scanner_d")
    if d:
        keys += [f"{s}:pullback" for s in d.get("long_pullback", [])]
        keys += [f"{s}:ext" for s in d.get("long_extended", [])]
        keys += [f"{s}:short" for s in d.get("short_watch", [])]
    return sorted(set(keys))


def _fmt_setup(key: str) -> str:
    sym, _, kind = key.partition(":")
    return f"{sym} ({_SETUP_WORDS.get(kind, kind)})"


def send_alert() -> bool:
    """Send the Trading Control summary, with intraday dedup.

    Open session: only send when a setup appears (TJL pass, long-watch or
    fade-short) that we haven't alerted on yet today, so the 30-min cadence
    doesn't repeat itself. Any other session (premarket briefing, after-hours,
    manual): always send, and seed the day's baseline so intraday only flags
    genuinely new ones. Returns True when a message was actually sent.
    """
    now = C.et_now()
    sess, _ = _session(now)
    today = now.strftime("%Y-%m-%d")
    current = _active_setups()
    state = _load_state()
    prev = set(state.get("alerted", [])) if state.get("date") == today else set()

    if sess == "Open":
        new = [k for k in current if k not in prev]
        if not new:
            print("[telegram] intraday: no new setup; skipping")
            return False
        msg = (to_telegram() + "\n\n<b>🆕 Nieuw:</b> "
               + ", ".join(_fmt_setup(k) for k in new))
        if not C.send_telegram(msg):
            return False
        _save_state({"date": today, "alerted": sorted(prev | set(current))})
        return True

    if not C.send_telegram(to_telegram()):
        return False
    _save_state({"date": today, "alerted": sorted(prev | set(current))})
    return True


if __name__ == "__main__":
    import sys
    public = "--public" in sys.argv
    out = None
    if "--out" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--out") + 1])
    p = build(public=public, out_path=out)
    print(f"Dashboard{' (public)' if public else ''} -> {p}")
    if "--telegram" in sys.argv:
        C.load_env()
        ok = send_alert()
        print(f"telegram sent: {ok}")
