#!/usr/bin/env python3
"""
LOCAL (not committed): after-the-fact review for the Trading Diary.

For each diary entry (analyses/log.json) that has a trade plan, is not yet
reviewed, and is >= REVIEW_AFTER_MIN old, fetch how the stock moved since it was
flagged and record the outcome vs the plan (target/stop hit, max move, a lesson).
Run each cycle; matured entries get reviewed as time passes.
"""
from __future__ import annotations

import sys
import json
from pathlib import Path

import yfinance as yf

import common as C

ROOT = Path(__file__).resolve().parent
LOG = ROOT.parent / "analyses" / "log.json"
SHADOW_LOG = ROOT.parent / "analyses" / "shadow_log.json"
REVIEW_AFTER_MIN = 45


def _bars_since(sym, ts):
    try:
        df = yf.download(sym, period="2d", interval="5m", prepost=True,
                         auto_adjust=False, progress=False)
        col = lambda x: x.iloc[:, 0] if hasattr(x, "columns") else x
        h, l = col(df["High"].dropna()), col(df["Low"].dropna())
        out = []
        for i in range(len(h)):
            t = int(h.index[i].timestamp())
            if t >= ts:
                out.append((t, float(h.iloc[i]), float(l.iloc[i])))
        return out
    except Exception:
        return []


def _review(e):
    entry, stop, target, d = e.get("entry"), e.get("stop"), e.get("target"), e.get("dir")
    if not entry or not stop:
        return {"outcome": "⏸️ Wacht-setup (geen trade-plan)", "result": "no_plan",
                "lesson": "Extended/najagen — geen instap; wachten op pullback naar VWAP."}
    bars = _bars_since(e["symbol"], e.get("flagged_ts", 0))
    if not bars:
        return {"outcome": "geen koersdata", "result": "no_data", "lesson": ""}
    hit = None
    mfe = mae = 0.0
    for _, hi, lo in bars:
        if d == "long":
            mfe = max(mfe, (hi - entry) / entry * 100)
            mae = min(mae, (lo - entry) / entry * 100)
            if lo <= stop:
                hit = ("stop", (stop - entry) / entry * 100); break
            if target and hi >= target:
                hit = ("target", (target - entry) / entry * 100); break
        else:  # short
            mfe = max(mfe, (entry - lo) / entry * 100)
            mae = min(mae, (entry - hi) / entry * 100)
            if hi >= stop:
                hit = ("stop", (entry - stop) / entry * 100); break
            if target and lo <= target:
                hit = ("target", (entry - target) / entry * 100); break
    base = {"mfe": round(mfe, 1), "mae": round(mae, 1)}
    if hit and hit[0] == "target":
        r_win = round(abs(target - entry) / (abs(entry - stop) or 0.01), 1)
        return {**base, "result": "win", "pct": round(hit[1], 1), "r_win": r_win,
                "outcome": f"🎯 Doel geraakt ({hit[1]:+.1f}%)",
                "lesson": "Plan werkte — instap bij VWAP hield, doel behaald. Zo scale je uit in kracht."}
    if hit and hit[0] == "stop":
        return {**base, "result": "loss", "pct": round(hit[1], 1),
                "outcome": f"🛑 Stop geraakt ({hit[1]:+.1f}%)",
                "lesson": "Invalidatie (VWAP/structuur niet gehouden). Stop gerespecteerd = discipline; kleine controleerbare loss."}
    return {**base, "result": "scratch",
            "outcome": f"➖ Geen hit (max {mfe:+.1f}% / {mae:+.1f}%)",
            "lesson": "Geen follow-through — mogelijk overextended of laag volume. Niet forceren; wachten op betere setup."}


def _append_backtest(e, rev, shadow=False):
    """Append one immutable record to the never-truncated backtest history."""
    rec = {"date": e.get("date"), "symbol": e.get("symbol"), "kind": e.get("kind"),
           "dir": e.get("dir"), "grade": e.get("grade"), "conviction": e.get("conviction"),
           "crowned": bool(e.get("crowned")), "catalyst_lean": e.get("catalyst_lean"),
           "rr": e.get("rr"), "result": rev.get("result"), "pct": rev.get("pct"),
           "mfe": rev.get("mfe"), "mae": rev.get("mae"), "r_win": rev.get("r_win"),
           "shadow": shadow}
    try:
        C.BACKTEST_LOG.parent.mkdir(exist_ok=True)
        with open(C.BACKTEST_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def _process(path, now_ts, shadow=False):
    """Review matured, not-yet-reviewed entries in one log file; append outcomes to the
    backtest history. Returns how many were reviewed this pass."""
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    changed = 0
    for e in log:
        if e.get("reviewed"):
            continue
        ft = e.get("flagged_ts")
        if not ft or (now_ts - ft) < REVIEW_AFTER_MIN * 60:
            continue
        rev = _review(e)
        e["review"] = rev
        e["reviewed"] = True
        changed += 1
        if rev.get("result") in ("win", "loss", "scratch"):
            _append_backtest(e, rev, shadow)
    if changed:
        path.write_text(json.dumps(log, indent=2), encoding="utf-8")
    return changed


def main():
    C.load_env()
    now_ts = int(C.et_now().timestamp())
    n = _process(LOG, now_ts) + _process(SHADOW_LOG, now_ts, shadow=True)
    print(f"reviewed {n} entr{'y' if n == 1 else 'ies'}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
