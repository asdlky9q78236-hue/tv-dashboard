#!/usr/bin/env python3
"""
Scanner D - VWAP momentum / fade watch on the day's gappers.

Takes the top gappers from Scanner A, pulls live 1-minute bars, computes the
session VWAP (the yellow line pros watch), and classifies each mover as:

  long_watch        above VWAP and pushing the day's high  -> momentum holding
  fade_short_watch  gapped up big but lost VWAP & faded     -> failed gap
  neutral / premarket otherwise

It also pulls SPY for market context so you know whether a trade would be with
or against the broad market.

IMPORTANT: this produces *alerts*, not trades. It encodes the mechanical
checklist a trader uses; on free, delayed data it cannot read Level 2 / order
flow or time sub-minute entries. Treat it as a smart watchlist, not advice.

Run:  python scanner_d.py
"""
from __future__ import annotations

import sys
import glob
import json
import datetime as dt

import yfinance as yf

import common as C

TOP_GAPPERS = 6
GAP_LONG_MIN = 3.0      # min gap% to consider a long watch
GAP_FADE_MIN = 5.0      # min gap% for a fade/short candidate
VWAP_NEAR = 0.015       # within 1.5% above VWAP = "back at VWAP"
PULLBACK_OFF_HIGH = 0.015  # pulled back >=1.5% off the day's high
OVEREXT_PM_PCT = 15.0   # premarket run-up over this = overextended (her rule)
OVEREXT_VWAP = 0.10     # trading >10% above VWAP = extended / chase risk


def _series(df, col):
    s = df[col].dropna()
    if hasattr(s, "columns"):
        s = s.iloc[:, 0]
    return s


def _session_vwap(intra):
    """Return (vwap, pmh, hod, recent_min) from 1m bars.

    vwap over the regular session; recent_min = lowest of the last 5 regular
    closes (used to test whether price is HOLDING VWAP after a pullback).
    """
    if intra is None or intra.empty:
        return None, None, None, None
    high = _series(intra, "High"); low = _series(intra, "Low")
    close = _series(intra, "Close"); vol = _series(intra, "Volume")
    idx = close.index
    try:
        idx_et = idx.tz_convert(C.ET)
    except Exception:
        idx_et = idx
    open_t = dt.time(9, 30)
    pre = [t.time() < open_t for t in idx_et]
    reg = [t.time() >= open_t for t in idx_et]
    pmh = float(high[pre].max()) if any(pre) else None
    if any(reg):
        h = high[reg]; l = low[reg]; c = close[reg]; v = vol[reg]
        hod = float(h.max())
        typ = (h + l + c) / 3.0
        denom = float(v.sum())
        vwap = float((typ * v).sum() / denom) if denom > 0 else None
        recent_min = float(c.tail(5).min())
    else:
        hod, vwap, recent_min = None, None, None
    return vwap, pmh, hod, recent_min


def _market_context() -> dict:
    """SPY snapshot: % on day + above/below its VWAP -> tailwind/headwind."""
    try:
        d = yf.download("SPY", period="5d", interval="1d",
                        auto_adjust=False, progress=False)
        i = yf.download("SPY", period="1d", interval="1m",
                        auto_adjust=False, progress=False)
        dclose = _series(d, "Close")
        prev = float(dclose.iloc[-2]) if len(dclose) >= 2 else float(dclose.iloc[-1])
        last = float(_series(i, "Close").iloc[-1])
        vwap, _, _, _ = _session_vwap(i)
        pct = round((last - prev) / prev * 100, 2)
        above = vwap is not None and last >= vwap
        if pct > 0 and above:
            tone = "rugwind"
        elif pct < 0 and not above:
            tone = "tegenwind"
        else:
            tone = "gemengd"
        return {"symbol": "SPY", "pct": pct, "last": round(last, 2),
                "above_vwap": above, "tone": tone}
    except Exception as e:
        return {"symbol": "SPY", "error": str(e)}


def evaluate(sym: str, prev_close, cap_class=None) -> dict:
    res = {"symbol": sym, "ok": False, "cap_class": cap_class}
    try:
        intra = yf.download(sym, period="1d", interval="1m", prepost=True,
                            auto_adjust=False, progress=False)
        close = _series(intra, "Close")
        if close.empty:
            res["error"] = "no intraday"
            return res
        last = float(close.iloc[-1])
        vwap, pmh, hod, recent_min = _session_vwap(intra)
        gap = round((last - prev_close) / prev_close * 100, 2) if prev_close else None
        above_vwap = vwap is not None and last >= vwap

        # overextension (her "avoid >15% premarket / don't chase extended")
        pm_runup = round((pmh - prev_close) / prev_close * 100, 2) if (pmh and prev_close) else None
        dist_vwap = (last - vwap) / vwap if vwap else None
        overextended = bool((pm_runup is not None and pm_runup > OVEREXT_PM_PCT)
                            or (dist_vwap is not None and dist_vwap > OVEREXT_VWAP))

        if vwap is None:
            kind, why = "premarket", ["beurs nog niet open - VWAP volgt na 09:30"]
        else:
            near_hod = hod is not None and last >= 0.995 * hod
            above_pmh = pmh is None or last >= pmh
            below_hod = hod is not None and last <= (1 - PULLBACK_OFF_HIGH) * hod
            near_vwap = above_vwap and dist_vwap is not None and dist_vwap <= VWAP_NEAR
            holding = recent_min is not None and recent_min >= vwap * 0.997
            big_gap = gap is not None and gap >= GAP_LONG_MIN

            if big_gap and near_vwap and below_hod and holding:
                kind = "long_pullback"        # her preferred entry: dip to VWAP that holds
                why = ["teruggetrokken naar VWAP", "houdt VWAP (bounce-zone)"]
            elif big_gap and above_vwap and near_hod and above_pmh:
                kind = "long_extended"        # at the highs - momentum but a chase
                why = ["boven VWAP", "op dagtop (extended - niet najagen)"]
            elif gap is not None and gap >= GAP_FADE_MIN and not above_vwap and below_hod:
                kind = "fade_short_watch"
                why = [f"gapte +{gap}%", "verloor VWAP", "teruggevallen van dagtop"]
            else:
                kind = "neutral"
                why = ["boven VWAP" if above_vwap else "onder VWAP"]
            if overextended and kind.startswith("long"):
                why.append("⚠️ overextended")

        res.update({
            "ok": True, "last": round(last, 2), "gap_pct": gap,
            "vwap": round(vwap, 2) if vwap else None,
            "premarket_high": round(pmh, 2) if pmh else None,
            "intraday_high": round(hod, 2) if hod else None,
            "premarket_runup_pct": pm_runup,
            "above_vwap": above_vwap, "overextended": overextended,
            "kind": kind, "why": why,
        })
    except Exception as e:
        res["error"] = str(e)
    return res


def _latest_a():
    files = sorted(glob.glob(str(C.OUT / "scanner_a_*.json")))
    if not files:
        return None
    try:
        return json.loads(open(files[-1], encoding="utf-8").read())
    except Exception:
        return None


def scan() -> dict:
    now = C.et_now()
    a = _latest_a()
    hits = (a or {}).get("hits", [])[:TOP_GAPPERS]
    market = _market_context()
    results = [evaluate(h["symbol"], h.get("prev_close"), h.get("cap_class")) for h in hits]
    def syms(kind):
        return [r["symbol"] for r in results if r.get("kind") == kind]
    return {
        "scanner": "D_vwap_watch",
        "generated_et": now.isoformat(),
        "market": market,
        "results": results,
        "long_pullback": syms("long_pullback"),
        "long_extended": syms("long_extended"),
        "short_watch": syms("fade_short_watch"),
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    C.load_env()
    rep = scan()
    C.write_json(f"scanner_d_{C.today_str()}.json", rep)
    m = rep["market"]
    print(f"Scanner D @ {rep['generated_et'][:16]} ET | "
          f"SPY {m.get('pct')}% ({m.get('tone')})")
    for r in rep["results"]:
        if not r.get("ok"):
            print(f"  {r['symbol']:6} ERR {r.get('error')}")
            continue
        print(f"  {r['symbol']:6} {r['kind']:16} ${r['last']:<8} "
              f"vwap {r.get('vwap')} | {', '.join(r['why'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
