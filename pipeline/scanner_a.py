#!/usr/bin/env python3
"""
Scanner A - Premarket Gap Scanner (HumbledTrader step 5).

Pulls premarket movers from Yahoo (via yfinance), applies the gap / price /
volume filters, attaches free-source news headlines, and writes the top-N
gappers to out/scanner_a_YYYYMMDD.json.

Run:  python scanner_a.py
With Telegram (step 11): set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env
and pass --telegram.
"""
from __future__ import annotations

import sys
import argparse
from pathlib import Path

import common as C
import config as CFG


def load_universe(universe_file: str | None = None) -> list[str]:
    base = Path(__file__).resolve().parent
    f = base / (universe_file or CFG.UNIVERSE_FILE or "universe.txt")
    if f.exists():
        syms = [s.strip().upper() for s in f.read_text().splitlines()
                if s.strip() and not s.startswith("#")]
        if syms:
            return sorted(set(syms))
    return sorted(set(CFG.DEFAULT_UNIVERSE))


def scan(universe_file: str | None = None) -> dict:
    universe = load_universe(universe_file)
    now = C.et_now()
    daily = C.get_daily_stats(universe)
    snap = C.get_premarket_snapshot(universe)

    mode = CFG.SCAN_MODE
    gap_floor = CFG.GAP_MIN_PCT_SMALLCAP if mode == "smallcap" else CFG.GAP_MIN_PCT

    # Stage 1: gap / price / liquidity / relative-volume screen
    prelim = []
    for sym in universe:
        d = daily.get(sym)
        s = snap.get(sym)
        if not d or not s:
            continue
        pc = d["prev_close"]
        last = s["last"]
        gap = (last - pc) / pc * 100.0
        if abs(gap) < gap_floor:
            continue
        if last < CFG.PRICE_MIN:
            continue
        if d["avg_vol"] < CFG.AVG_VOL_MIN:
            continue
        if d.get("rvol", 0) < CFG.RVOL_MIN:
            continue
        prelim.append({
            "symbol": sym,
            "gap_pct": round(gap, 2),
            "price": round(last, 2),
            "prev_close": round(pc, 2),
            "avg_vol": d["avg_vol"],
            "rvol": d.get("rvol", 0),
            "source": s["source"],
        })

    # Stage 2: market-cap class filter (large-cap > $10B / small-cap < $800M)
    caps = C.get_market_caps([h["symbol"] for h in prelim]) if prelim else {}

    def mcap_ok(mc):
        if mode == "largecap":
            return mc is not None and mc >= CFG.MARKET_CAP_MIN_USD
        if mode == "smallcap":
            return mc is not None and mc <= CFG.SMALLCAP_MAX_USD
        return True

    hits = []
    for h in prelim:
        mc = caps.get(h["symbol"])
        if not mcap_ok(mc):
            continue
        h["market_cap_b"] = round(mc / 1e9, 2) if mc else None
        hits.append(h)

    hits.sort(key=lambda h: abs(h["gap_pct"]), reverse=True)
    top = hits[:CFG.TOP_N]

    # attach news catalysts only for the shortlist (keeps it fast)
    for h in top:
        h["news"] = C.get_news(h["symbol"], limit=2)

    return {
        "scanner": "A_premarket_gap",
        "generated_et": now.isoformat(),
        "is_premarket": C.is_premarket(now),
        "universe_size": len(universe),
        "filters": {
            "mode": mode,
            "gap_min_pct": gap_floor,
            "price_min": CFG.PRICE_MIN,
            "avg_vol_min": CFG.AVG_VOL_MIN,
            "rvol_min": CFG.RVOL_MIN,
            "market_cap_min_usd": CFG.MARKET_CAP_MIN_USD if mode == "largecap" else None,
            "smallcap_max_usd": CFG.SMALLCAP_MAX_USD if mode == "smallcap" else None,
        },
        "count": len(top),
        "hits": top,
    }


def to_telegram(report: dict) -> str:
    lines = [f"<b>Scanner A - Premarket Gaps</b>",
             f"<i>{report['generated_et'][:16].replace('T', ' ')} ET</i>"]
    if not report["hits"]:
        lines.append("No gappers passed filters.")
        return "\n".join(lines)
    for h in report["hits"]:
        sign = "+" if h["gap_pct"] >= 0 else ""
        mc = f"  ${h['market_cap_b']}B" if h.get("market_cap_b") else ""
        line = (f"\n<b>{h['symbol']}</b>  {sign}{h['gap_pct']}%  "
                f"${h['price']}  rvol {h['rvol']}x{mc}")
        lines.append(line)
        if h.get("news"):
            n = h["news"][0]
            lines.append(f"  <i>{n['title']}</i>")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram", action="store_true",
                    help="send report to Telegram")
    ap.add_argument("--universe", default=None,
                    help="universe filename (e.g. watchlist_momentum.txt)")
    args = ap.parse_args()

    try:  # avoid cp1252 mojibake on Windows consoles
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    C.load_env()
    report = scan(args.universe)
    path = C.write_json(f"scanner_a_{C.today_str()}.json", report)

    print(f"Scanner A: {report['count']} hit(s) -> {path}")
    for h in report["hits"]:
        cat = h["news"][0]["title"] if h.get("news") else "-"
        mc = f"{h['market_cap_b']}B" if h.get("market_cap_b") else "-"
        print(f"  {h['symbol']:6} {h['gap_pct']:+6.2f}%  ${h['price']:<8} "
              f"rvol {h['rvol']:>4}x  mcap {mc:>8}  | {cat[:42]}")

    if args.telegram:
        C.send_telegram(to_telegram(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
