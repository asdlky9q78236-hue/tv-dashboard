#!/usr/bin/env python3
"""
Performance summary + dashboard (adapted from the HumbledTrader IBKR bot, no
broker needed). Reads a trades log, pairs BUY/SELL FIFO, computes R-multiples,
win rate and profit factor, sends a Telegram summary, and writes an HTML
dashboard.

trades.csv schema (one row per fill):
    timestamp_iso,symbol,side,size,fill_price,order_id,status,stop_price

`side` is BUY or SELL. `stop_price` is optional (used for per-trade R; if
missing, a 1% initial-stop proxy is used).

Run:  python compute_perf.py [--telegram]
"""
from __future__ import annotations

import csv
import sys
import html
import argparse
from collections import defaultdict, deque
from pathlib import Path

import common as C

ROOT = Path(__file__).resolve().parent
TRADES = ROOT / "trades.csv"
DASH = ROOT / "dashboard"
R_BUCKETS = [("<=-2R", -1e9, -2), ("-2..-1R", -2, -1), ("-1..0R", -1, 0),
             ("0..1R", 0, 1), ("1..2R", 1, 2), ("2..3R", 2, 3),
             (">3R", 3, 1e9)]


def load_trades() -> list[dict]:
    if not TRADES.exists():
        return []
    rows = []
    with TRADES.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r.get("symbol"):
                continue
            rows.append(r)
    return rows


def pair_trades(rows: list[dict]) -> list[dict]:
    """FIFO-pair BUYs with later SELLs per symbol into closed round-trips."""
    opens: dict[str, deque] = defaultdict(deque)
    closed = []
    for r in rows:
        sym = r["symbol"].upper()
        side = (r.get("side") or "").upper()
        try:
            size = float(r.get("size") or 0)
            price = float(r.get("fill_price") or 0)
        except ValueError:
            continue
        stop = r.get("stop_price")
        stop = float(stop) if stop not in (None, "", "0") else None
        if side == "BUY":
            opens[sym].append({"size": size, "price": price, "stop": stop,
                               "ts": r.get("timestamp_iso", "")})
        elif side == "SELL":
            remaining = size
            while remaining > 1e-9 and opens[sym]:
                lot = opens[sym][0]
                q = min(remaining, lot["size"])
                buy, stop = lot["price"], lot["stop"]
                risk = (buy - stop) if stop else buy * 0.01
                r_mult = (price - buy) / risk if risk > 0 else 0.0
                closed.append({
                    "symbol": sym, "qty": q, "buy": buy, "sell": price,
                    "pnl": (price - buy) * q,
                    "pnl_pct": (price - buy) / buy * 100 if buy else 0.0,
                    "R": r_mult, "ts": lot["ts"],
                })
                lot["size"] -= q
                remaining -= q
                if lot["size"] <= 1e-9:
                    opens[sym].popleft()
    return closed


def aggregate(closed: list[dict]) -> dict:
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = sum(t["pnl"] for t in losses)
    pf = (gross_win / abs(gross_loss)) if gross_loss else (float("inf") if gross_win else 0.0)
    hist = {b[0]: 0 for b in R_BUCKETS}
    for t in closed:
        for name, lo, hi in R_BUCKETS:
            if lo < t["R"] <= hi:
                hist[name] += 1
                break
    best = max(closed, key=lambda t: t["pnl"], default=None)
    worst = min(closed, key=lambda t: t["pnl"], default=None)
    return {
        "total": len(closed), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
        "gross_pnl": round(sum(t["pnl"] for t in closed), 2),
        "profit_factor": round(pf, 2) if pf != float("inf") else None,
        "avg_winner": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loser": round(gross_loss / len(losses), 2) if losses else 0.0,
        "best": best, "worst": worst, "r_hist": hist,
    }


def build_html(agg: dict, closed: list[dict]) -> str:
    rows = ""
    for t in closed[-20:][::-1]:
        color = "success" if t["pnl"] > 0 else "danger"
        rows += (f"<tr><td>{html.escape(t['symbol'])}</td>"
                 f"<td>{t['qty']:.4g}</td><td>${t['buy']:.2f}</td>"
                 f"<td>${t['sell']:.2f}</td>"
                 f"<td class='text-{color}'>${t['pnl']:+.2f}</td>"
                 f"<td class='text-{color}'>{t['R']:+.2f}R</td></tr>")
    maxc = max(agg["r_hist"].values()) or 1
    bars = ""
    for name, n in agg["r_hist"].items():
        w = int(n / maxc * 100)
        pos = not name.startswith(("<", "-"))
        bars += (f"<div class='mb-1'><small>{name}</small>"
                 f"<div class='progress'><div class='progress-bar bg-{'success' if pos else 'danger'}' "
                 f"style='width:{w}%'>{n or ''}</div></div></div>")
    pf = agg["profit_factor"]
    pf_s = "∞" if pf is None else pf
    best = agg["best"]; worst = agg["worst"]
    best_s = f"{best['symbol']} ${best['pnl']:+.2f}" if best else "-"
    worst_s = f"{worst['symbol']} ${worst['pnl']:+.2f}" if worst else "-"
    pnl_color = "success" if agg["gross_pnl"] >= 0 else "danger"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TV Scanner — Performance</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head><body class="bg-light"><div class="container py-4">
<h3 class="mb-3">📊 Strategy Performance <small class="text-muted fs-6">({C.et_now():%Y-%m-%d %H:%M} ET)</small></h3>
<div class="row g-3">
  <div class="col-md-3"><div class="card h-100"><div class="card-body">
    <h6 class="text-muted">Net P&amp;L</h6><h3 class="text-{pnl_color}">${agg['gross_pnl']:+,.2f}</h3>
    <div>{agg['total']} trades · {agg['wins']}W / {agg['losses']}L</div></div></div></div>
  <div class="col-md-3"><div class="card h-100"><div class="card-body">
    <h6 class="text-muted">Win rate</h6><h3>{agg['win_rate']}%</h3>
    <div>Profit factor {pf_s}</div></div></div></div>
  <div class="col-md-3"><div class="card h-100"><div class="card-body">
    <h6 class="text-muted">Avg win / loss</h6><h3>${agg['avg_winner']:.0f} / ${agg['avg_loser']:.0f}</h3>
    <div>Best {best_s}<br>Worst {worst_s}</div></div></div></div>
  <div class="col-md-3"><div class="card h-100"><div class="card-body">
    <h6 class="text-muted">R-multiple histogram</h6>{bars}</div></div></div>
</div>
<div class="card mt-3"><div class="card-body">
  <h6 class="text-muted">Recent closed trades</h6>
  <table class="table table-sm"><thead><tr><th>Symbol</th><th>Qty</th><th>Buy</th><th>Sell</th><th>P&amp;L</th><th>R</th></tr></thead>
  <tbody>{rows or '<tr><td colspan=6 class="text-muted">No closed trades yet — log fills to trades.csv</td></tr>'}</tbody></table>
</div></div>
</div></body></html>"""


def to_telegram(agg: dict) -> str:
    pf = agg["profit_factor"]; pf_s = "∞" if pf is None else pf
    best = agg["best"]; worst = agg["worst"]
    lines = [f"<b>Daily Summary — {C.et_now():%Y-%m-%d}</b>",
             f"Trades: {agg['total']} ({agg['wins']}W / {agg['losses']}L, {agg['win_rate']}%)",
             f"P&L: ${agg['gross_pnl']:+,.2f}   PF: {pf_s}"]
    if best:
        lines.append(f"Best: {best['symbol']} ${best['pnl']:+.2f}")
    if worst:
        lines.append(f"Worst: {worst['symbol']} ${worst['pnl']:+.2f}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    C.load_env()
    closed = pair_trades(load_trades())
    agg = aggregate(closed)

    DASH.mkdir(exist_ok=True)
    (DASH / "index.html").write_text(build_html(agg, closed), encoding="utf-8")

    print(f"Closed trades: {agg['total']}  win {agg['win_rate']}%  "
          f"PnL ${agg['gross_pnl']:+,.2f}  PF {agg['profit_factor']}")
    print(f"Dashboard -> {DASH / 'index.html'}")

    if args.telegram and agg["total"] > 0:
        C.send_telegram(to_telegram(agg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
