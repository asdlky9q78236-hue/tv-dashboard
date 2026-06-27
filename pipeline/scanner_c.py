#!/usr/bin/env python3
"""
Scanner C - Crypto pair setup scanner (daily).

Finds crypto pairs that reliably produce Trend Join Long setups on the daily
timeframe. For each pair it replays the TJL-crypto entry logic over a lookback
window and reports:

  - setups       : number of breakout setups in the window
  - per_month    : setup frequency (setups per 30 days)
  - win_rate     : % of setups higher N days later (reliability)
  - avg_fwd      : average forward return N days after a setup
  - live_setup   : whether a setup is active right now
  - days_since   : days since the last setup

Entry logic (per bar): close > 200-SMA (trend) AND close > prior Donchian high
(breakout) AND BTC > its own 200-SMA (regime). Consecutive trigger bars are
collapsed (cooldown) so one breakout = one setup.

Run:  python scanner_c.py [--telegram] [--min-per-month 1.0]
"""
from __future__ import annotations

import sys
import argparse

import numpy as np
import pandas as pd
import yfinance as yf

import common as C
import config as CFG


def _series(df, col):
    s = df[col]
    if hasattr(s, "columns"):
        s = s.iloc[:, 0]
    return s.dropna()


def _regime_mask(sma_len: int) -> pd.Series:
    """Boolean series (by date): BTC close > its sma_len-SMA."""
    btc = yf.download(CFG.CRYPTO_REGIME, period="900d", interval="1d",
                      auto_adjust=True, progress=False)
    if btc is None or btc.empty:
        return pd.Series(dtype=bool)
    c = _series(btc, "Close")
    sma = c.rolling(sma_len).mean()
    return (c > sma)


def evaluate(symbol: str, regime: pd.Series, p: dict) -> dict:
    res = {"symbol": symbol, "ok": False, "error": None}
    try:
        df = yf.download(symbol, period="900d", interval="1d",
                         auto_adjust=True, progress=False)
        if df is None or df.empty:
            res["error"] = "no data"
            return res
        close = _series(df, "Close")
        high = _series(df, "High")
        if len(close) < p["sma"] + 30:
            res["error"] = "short history"
            return res

        sma = close.rolling(p["sma"]).mean()
        don_high = high.rolling(p["don"]).max().shift(1)
        trigger = (close > sma) & (close > don_high)
        if p["use_regime"]:
            reg = regime.reindex(close.index).ffill().fillna(False)
            trigger = trigger & reg.astype(bool)
        # collapse consecutive triggers -> distinct setup events (rising edge)
        event = trigger & (~trigger.shift(1, fill_value=False))

        window = close.index >= (close.index[-1] - pd.Timedelta(days=p["lookback"]))
        ev_idx = close.index[event & window]

        # forward-return reliability
        wins, rets = 0, []
        fwd = p["fwd"]
        arr = close.values
        pos = {ts: i for i, ts in enumerate(close.index)}
        for ts in ev_idx:
            i = pos[ts]
            if i + fwd < len(arr):
                r = (arr[i + fwd] - arr[i]) / arr[i] * 100.0
                rets.append(r)
                if r > 0:
                    wins += 1
        n = len(ev_idx)
        scored = len(rets)
        per_month = round(n / (p["lookback"] / 30.0), 2)
        win_rate = round(wins / scored * 100, 1) if scored else 0.0
        avg_fwd = round(float(np.mean(rets)), 2) if rets else 0.0

        last_ev = ev_idx[-1] if n else None
        days_since = int((close.index[-1] - last_ev).days) if last_ev is not None else None
        live = bool(trigger.iloc[-1])

        res.update({
            "ok": True,
            "price": round(float(close.iloc[-1]), 4),
            "above_200sma": bool(close.iloc[-1] > sma.iloc[-1]),
            "setups": n,
            "per_month": per_month,
            "win_rate": win_rate,
            "avg_fwd": avg_fwd,
            "live_setup": live,
            "days_since": days_since,
            # composite score: frequency weighted by reliability edge
            "score": round(per_month * (win_rate / 100.0) * max(avg_fwd, 0.1), 2),
        })
    except Exception as e:
        res["error"] = str(e)
    return res


def scan(p: dict, min_per_month: float) -> dict:
    regime = _regime_mask(p["sma"]) if p["use_regime"] else pd.Series(dtype=bool)
    rows = [evaluate(s, regime, p) for s in CFG.CRYPTO_UNIVERSE]
    ok = [r for r in rows if r.get("ok")]
    ok.sort(key=lambda r: r["score"], reverse=True)
    reliable = [r for r in ok if r["per_month"] >= min_per_month]
    return {
        "scanner": "C_crypto_pairs",
        "generated_et": C.et_now().isoformat(),
        "params": {
            "sma_len": p["sma"], "don_len": p["don"],
            "lookback_days": p["lookback"], "fwd_days": p["fwd"],
            "use_regime": p["use_regime"], "min_per_month": min_per_month,
        },
        "universe_size": len(CFG.CRYPTO_UNIVERSE),
        "ranked": ok,
        "reliable": [r["symbol"] for r in reliable],
        "live_setups": [r["symbol"] for r in ok if r["live_setup"]],
    }


def to_telegram(report: dict) -> str:
    lines = ["<b>Scanner C - Crypto pairs (daily setups)</b>",
             f"<i>{report['generated_et'][:16].replace('T', ' ')} ET</i>"]
    live = report["live_setups"]
    lines.append(f"\n<b>Live setups:</b> {', '.join(live) if live else 'none'}")
    lines.append("\n<b>Top by reliability:</b>")
    for r in report["ranked"][:8]:
        star = " 🔥" if r["live_setup"] else ""
        lines.append(f"{r['symbol']}: {r['per_month']}/mo, "
                     f"win {r['win_rate']}%, +{r['avg_fwd']}%{star}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--min-per-month", type=float, default=1.0)
    ap.add_argument("--sma", type=int, default=CFG.CRYPTO_SMA_LEN)
    ap.add_argument("--don", type=int, default=CFG.CRYPTO_DON_LEN)
    ap.add_argument("--lookback", type=int, default=CFG.CRYPTO_LOOKBACK_DAYS)
    ap.add_argument("--fwd", type=int, default=CFG.CRYPTO_FWD_DAYS)
    ap.add_argument("--no-regime", action="store_true",
                    help="drop the BTC regime gate (more setups)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    p = {"sma": args.sma, "don": args.don, "lookback": args.lookback,
         "fwd": args.fwd, "use_regime": not args.no_regime}
    C.load_env()
    report = scan(p, args.min_per_month)
    C.write_json(f"scanner_c_{C.today_str()}.json", report)

    print(f"Scanner C @ {report['generated_et'][:16]} ET  "
          f"({report['universe_size']} pairs)")
    print(f"{'PAIR':12} {'/mo':>5} {'win%':>6} {'avgfwd':>7} {'score':>6} "
          f"{'live':>5} {'since':>6}")
    for r in report["ranked"]:
        if not r.get("ok"):
            continue
        print(f"{r['symbol']:12} {r['per_month']:>5} {r['win_rate']:>6} "
              f"{r['avg_fwd']:>6}% {r['score']:>6} "
              f"{'YES' if r['live_setup'] else '-':>5} "
              f"{r['days_since'] if r['days_since'] is not None else '-':>6}")
    print(f"\nLive setups: {', '.join(report['live_setups']) or 'none'}")
    print(f"Reliable (>= {args.min_per_month}/mo): {', '.join(report['reliable'])}")

    if args.telegram:
        C.send_telegram(to_telegram(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
