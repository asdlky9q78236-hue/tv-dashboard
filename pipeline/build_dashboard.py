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


def scanner_a_html(rep):
    if not rep or not rep.get("hits"):
        return card("📈 Gap screener", '<div class="text-muted">No candidates.</div>')
    rows = ""
    for h in rep["hits"]:
        sign = "+" if h["gap_pct"] >= 0 else ""
        col = "success" if h["gap_pct"] >= 0 else "danger"
        mc = f"${h['market_cap_b']}B" if h.get("market_cap_b") else "-"
        cat = (h["news"][0]["title"] if h.get("news") else "")[:60]
        rows += (f"<tr><td><b>{html.escape(h['symbol'])}</b></td>"
                 f"<td class='text-{col}'>{sign}{h['gap_pct']}%</td>"
                 f"<td>{h['rvol']}x</td><td>{mc}</td>"
                 f"<td class='small text-muted'>{html.escape(cat)}</td></tr>")
    body = (f"<div class='small text-muted mb-2'>mode {rep['filters'].get('mode')} · "
            f"gap≥{rep['filters'].get('gap_min_pct')}% · {rep['count']} hits</div>"
            f"<div class='table-responsive'><table class='table table-dark table-sm align-middle'>"
            f"<thead><tr><th>Sym</th><th>Gap</th><th>RVol</th><th>Cap</th><th>Catalyst</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>")
    return card("📈 Gap screener", body)


def scanner_b_html(rep):
    if not rep:
        return card("🎯 TJL setups", '<div class="text-muted">No data.</div>')
    rows = ""
    for r in rep.get("results", []):
        if not r.get("ok"):
            rows += f"<tr><td>{html.escape(r['symbol'])}</td><td colspan=3 class='text-muted'>error</td></tr>"
            continue
        badge = ("PASS", "success") if r["PASS"] else (("daily", "warning") if r["daily_pass"] else ("—", "secondary"))
        rows += (f"<tr><td><b>{html.escape(r['symbol'])}</b></td>"
                 f"<td><span class='badge bg-{badge[1]}'>{badge[0]}</span></td>"
                 f"<td>${r['last']}</td><td>{r.get('rvol','-')}x</td></tr>")
    hits = ", ".join(rep.get("hits", [])) or "none"
    body = (f"<div class='small text-muted mb-2'>passing: {hits}</div>"
            f"<table class='table table-dark table-sm'><tbody>{rows}</tbody></table>")
    return card("🎯 TJL setups", body)


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

    parts = [scanner_a_html(a), scanner_b_html(b), scanner_c_html(c)]
    if not public:                       # keep P&L private off the public URL
        parts.append(perf_html())
    cards = "".join(parts)
    htmldoc = f"""<!doctype html><html lang="en"><head>
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
        lines.append("\n<b>Gap screener:</b>")
        for h in top:
            s = "+" if h["gap_pct"] >= 0 else ""
            lines.append(f"  {h['symbol']} {s}{h['gap_pct']}%  rvol {h['rvol']}x")
    if b and b.get("hits"):
        lines.append(f"\n<b>TJL setups:</b> {', '.join(b['hits'])}")
    if c and c.get("live_setups"):
        lines.append(f"<b>Crypto live:</b> {', '.join(c['live_setups'])}")
    agg = P.aggregate(P.pair_trades(P.load_trades()))
    if agg["total"]:
        pf = agg["profit_factor"]
        lines.append(f"\n<b>P&L:</b> ${agg['gross_pnl']:+,.0f} · "
                     f"win {agg['win_rate']}% · PF {'∞' if pf is None else pf}")
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


def _tjl_setups() -> list[str]:
    b = _latest("scanner_b")
    return sorted(b.get("hits", [])) if b else []


def send_alert() -> bool:
    """Send the Trading Control summary, with intraday dedup.

    Open session: only send when a TJL setup appears that we haven't alerted on
    yet today (avoids repeating the same setups every 30 min). Any other session
    (premarket morning briefing, after-hours, manual): always send, and seed the
    day's TJL baseline so intraday only flags genuinely new ones.
    Returns True when a message was actually sent.
    """
    now = C.et_now()
    sess, _ = _session(now)
    today = now.strftime("%Y-%m-%d")
    current = _tjl_setups()
    state = _load_state()
    prev = set(state.get("alerted_tjl", [])) if state.get("date") == today else set()

    if sess == "Open":
        new = [s for s in current if s not in prev]
        if not new:
            print("[telegram] intraday: no new TJL setup; skipping")
            return False
        msg = to_telegram() + f"\n\n<b>🆕 New TJL:</b> {', '.join(new)}"
        if not C.send_telegram(msg):
            return False
        _save_state({"date": today, "alerted_tjl": sorted(prev | set(current))})
        return True

    if not C.send_telegram(to_telegram()):
        return False
    _save_state({"date": today, "alerted_tjl": sorted(prev | set(current))})
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
