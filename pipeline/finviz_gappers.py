#!/usr/bin/env python3
"""
Finviz Elite gapper feed — dynamic premarket-gapper discovery.

Queries the Finviz Elite export API (needs env FINVIZ_AUTH_TOKEN) for stocks
gapping up on volume, returning the real movers of the day — including the
small-cap, low-float runners a static universe can never catch. This is the
dynamic discovery that HumbledTrader's strategy actually relies on.

Returns None on any failure (no token, HTTP error, unexpected payload, e.g. the
7-day trial expired or a datacenter IP got blocked) so scanner_a can fall back
to its static universe scan and the pipeline keeps working.
"""
from __future__ import annotations

import os
import csv
import io
import urllib.request

# Finviz custom-export (v=152) column indices:
#   1 Ticker · 6 Market Cap · 25 Shares Float · 61 Gap · 63 Avg Volume
#   64 Relative Volume · 65 Price · 81 Prev Close
_COLS = "1,6,25,61,63,64,65,81"
# gap up >=4%, price >$2, rel vol >1.5, avg vol >100k shares, real stocks (no ETFs)
_FILTERS = "ta_gap_u4,sh_price_o2,sh_relvol_o1.5,sh_avgvol_o100,ind_stocksonly"


def _num(x):
    x = (x or "").strip().replace("%", "").replace(",", "")
    try:
        return float(x)
    except ValueError:
        return None


def _cap_class(mc_m):
    """mc_m = market cap in millions (Finviz unit)."""
    if mc_m is None:
        return None
    if mc_m < 2000:
        return "small"
    if mc_m < 10000:
        return "mid"
    return "large"


def fetch_gappers(top_n: int = 15, min_gap: float = 4.0,
                  filters: str = _FILTERS, timeout: int = 30):
    """Return a list of gapper hit dicts (sorted by |gap|), or None on failure."""
    token = os.environ.get("FINVIZ_AUTH_TOKEN", "").strip()
    if not token:
        return None
    url = (f"https://elite.finviz.com/export.ashx?v=152&c={_COLS}"
           f"&f={filters}&o=-gap&auth={token}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"[finviz] fetch failed: {e}")
        return None
    rows = list(csv.reader(io.StringIO(body)))
    if not rows or rows[0][:1] != ["Ticker"]:
        print("[finviz] unexpected response (token expired / blocked?)")
        return None
    out = []
    for r in rows[1:]:
        if len(r) < 8:
            continue
        sym, mc, flt, gap, avgv, relv, price, prev = r[:8]
        gp = _num(gap)
        if gp is None or gp < min_gap:
            continue
        mc_m = _num(mc)
        avg = _num(avgv)
        out.append({
            "symbol": sym.strip().upper(),
            "gap_pct": round(gp, 2),
            "price": _num(price),
            "prev_close": _num(prev),
            "rvol": _num(relv),
            "avg_vol": int(avg * 1000) if avg else None,
            "float_m": _num(flt),
            "market_cap_b": round(mc_m / 1000, 2) if mc_m else None,
            "cap_class": _cap_class(mc_m),
            "source": "finviz",
        })
    out.sort(key=lambda h: abs(h["gap_pct"]), reverse=True)
    return out[:top_n]


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import common as C
    C.load_env()
    g = fetch_gappers()
    if g is None:
        print("Finviz: no token or fetch failed")
    else:
        print(f"Finviz: {len(g)} gappers")
        for h in g:
            print(f"  {h['symbol']:6} {h['gap_pct']:+7.2f}%  ${h['price']:<8} "
                  f"rvol {h['rvol']}  float {h['float_m']}M  {h['cap_class']}")
