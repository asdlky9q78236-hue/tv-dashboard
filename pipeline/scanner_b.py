#!/usr/bin/env python3
"""
Scanner B - Trend Join Long strategy scanner (HumbledTrader step 7).

For each ticker (default AMD/NVDA/MU) pulls daily + 1m data, computes the
200-SMA on both, finds the premarket high and intraday high, and evaluates
the Trend Join Long (TJL) entry conditions:

  Daily breakout (trend join setup):
    D1  last > daily 200-SMA          (long-term uptrend)
    D2  last >= prior-day high        (daily-range breakout)

  Intraday breakout (momentum join):
    I1  last > intraday(1m) 200-SMA   (intraday uptrend)
    I2  last >= premarket high        (cleared PMH)
    I3  last >= 99.9% of intraday high (at/near HOD)

  PASS = (D1 & D2) and (I1 & I2 & I3)

Run:  python scanner_b.py [--telegram]
"""
from __future__ import annotations

import sys
import argparse
import datetime as dt

import yfinance as yf

import common as C
import config as CFG


def _sma(series, length):
    if len(series) < length:
        return None
    return float(series.tail(length).mean())


def evaluate(symbol: str) -> dict:
    res = {"symbol": symbol, "ok": False, "error": None}
    try:
        daily = yf.download(symbol, period="400d", interval="1d",
                            auto_adjust=False, progress=False)
        intra = yf.download(symbol, period="1d", interval="1m", prepost=True,
                            auto_adjust=False, progress=False)
        if daily is None or daily.empty or intra is None or intra.empty:
            res["error"] = "no data"
            return res

        dclose = daily["Close"].dropna()
        dhigh = daily["High"].dropna()
        dvol = daily["Volume"].dropna()
        if hasattr(dclose, "columns"):
            dclose = dclose.iloc[:, 0]
            dhigh = dhigh.iloc[:, 0]
            dvol = dvol.iloc[:, 0]

        daily_200 = _sma(dclose, CFG.SMA_LEN)
        prior_day_high = float(dhigh.iloc[-1])

        # Relative volume: latest session vs the prior 14 sessions
        rvol = 0.0
        if len(dvol) > 14:
            base = float(dvol.iloc[-15:-1].mean())
            rvol = round(float(dvol.iloc[-1]) / base, 2) if base > 0 else 0.0

        ic = intra["Close"].dropna()
        ih = intra["High"].dropna()
        if hasattr(ic, "columns"):
            ic = ic.iloc[:, 0]
            ih = ih.iloc[:, 0]

        idx = ic.index
        try:
            idx_et = idx.tz_convert(C.ET)
        except Exception:
            idx_et = idx
        open_t = dt.time(9, 30)
        pre_mask = [t.time() < open_t for t in idx_et]
        reg_mask = [t.time() >= open_t for t in idx_et]

        pmh = float(ih[pre_mask].max()) if any(pre_mask) else None
        hod = float(ih[reg_mask].max()) if any(reg_mask) else float(ih.max())
        intraday_200 = _sma(ic, CFG.SMA_LEN)
        last = float(ic.iloc[-1])

        D1 = daily_200 is not None and last > daily_200
        D2 = last >= prior_day_high
        I1 = intraday_200 is not None and last > intraday_200
        I2 = pmh is not None and last >= pmh
        I3 = hod is not None and last >= 0.999 * hod
        I4 = rvol >= CFG.SCANNER_B_RVOL_MIN
        daily_pass = D1 and D2
        intraday_pass = I1 and I2 and I3 and I4

        res.update({
            "ok": True,
            "last": round(last, 2),
            "rvol": rvol,
            "daily_200_sma": round(daily_200, 2) if daily_200 else None,
            "prior_day_high": round(prior_day_high, 2),
            "intraday_200_sma": round(intraday_200, 2) if intraday_200 else None,
            "premarket_high": round(pmh, 2) if pmh else None,
            "intraday_high": round(hod, 2) if hod else None,
            "conditions": {
                "D1_above_daily200": D1, "D2_above_prior_high": D2,
                "I1_above_intraday200": I1, "I2_above_pmh": I2,
                "I3_at_hod": I3, "I4_rvol_ok": I4,
            },
            "daily_pass": daily_pass,
            "intraday_pass": intraday_pass,
            "PASS": daily_pass and intraday_pass,
        })
    except Exception as e:
        res["error"] = str(e)
    return res


def scan() -> dict:
    now = C.et_now()
    results = [evaluate(t) for t in CFG.SCANNER_B_TICKERS]
    hits = [r for r in results if r.get("PASS")]
    return {
        "scanner": "B_trend_join_long",
        "generated_et": now.isoformat(),
        "tickers": CFG.SCANNER_B_TICKERS,
        "results": results,
        "hits": [h["symbol"] for h in hits],
        "hit_count": len(hits),
    }


def to_telegram(report: dict) -> str:
    lines = ["<b>Scanner B - Trend Join Long</b>",
             f"<i>{report['generated_et'][:16].replace('T', ' ')} ET</i>"]
    if not report["hits"]:
        lines.append("No TJL setups passed.")
    for r in report["results"]:
        if not r.get("ok"):
            lines.append(f"\n<b>{r['symbol']}</b>  data error")
            continue
        tag = "PASS" if r["PASS"] else ("daily-only" if r["daily_pass"]
                                        else ("intra-only" if r["intraday_pass"]
                                              else "no setup"))
        lines.append(f"\n<b>{r['symbol']}</b>  {tag}  ${r['last']} "
                     f"(d200 {r['daily_200_sma']}, PMH {r['premarket_high']})")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--gate", action="store_true",
                    help="only send Telegram on first daily run or new hits")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    C.load_env()
    report = scan()
    C.write_json(f"scanner_b_{C.today_str()}.json", report)

    print(f"Scanner B @ {report['generated_et'][:16]} ET")
    for r in report["results"]:
        if not r.get("ok"):
            print(f"  {r['symbol']:5} ERROR {r['error']}")
            continue
        c = r["conditions"]
        flags = "".join(k.split('_')[0] + ("+" if v else "-") + " "
                        for k, v in c.items())
        print(f"  {r['symbol']:5} {'PASS' if r['PASS'] else '----':4} "
              f"${r['last']:<8} | {flags}")

    if args.telegram and _should_send(report, args.gate):
        C.send_telegram(to_telegram(report))
    return 0


def _should_send(report: dict, gate: bool) -> bool:
    """Notification gating for step 8: first daily run or new hits only."""
    if not gate:
        return True
    state = C.OUT / f"scanner_b_state_{C.today_str()}.json"
    seen = set()
    if state.exists():
        import json
        try:
            seen = set(json.loads(state.read_text()).get("seen_hits", []))
        except Exception:
            seen = set()
        first_run = False
    else:
        first_run = True
    new_hits = set(report["hits"]) - seen
    import json
    state.write_text(json.dumps({"seen_hits": sorted(seen | set(report["hits"]))}))
    return first_run or bool(new_hits)


if __name__ == "__main__":
    sys.exit(main())
