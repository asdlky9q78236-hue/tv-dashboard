#!/usr/bin/env python3
"""
LOCAL stage 1 (not committed): detect NEW signals to analyse this cycle.

Runs scanner_a (Finviz gappers) + scanner_d (VWAP classification) and writes the
new, deduplicated signals to out/tv_new_signals.json as:
  [{symbol, kind, label, gap_pct, cap_class}]

The driver then, per signal, pulls LIVE bars from the TradingView chart and runs
tv_compute.py to build the marker + findings from that live data (not Yahoo).
"""
from __future__ import annotations

import sys
import json
import subprocess
from pathlib import Path

import common as C

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "out" / "tv_analyzed_state.json"
OUTFILE = ROOT / "out" / "tv_new_signals.json"
SHADOW_LOG = ROOT.parent / "analyses" / "shadow_log.json"
SHADOW_STATE = ROOT / "out" / "shadow_state.json"

KIND_LABEL = {"long_pullback": "VWAP-pullback", "long_extended": "Long (extended)",
              "fade_short_watch": "Fade-short"}
# NB: only actionable setups get their own card. 'long_extended' = wait/don't-chase,
# so it stays on the Watchboard only (not a per-signal card).
KIND_FIELD = {"long_pullback": "long_pullback", "fade_short_watch": "short_watch"}


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(s: dict):
    STATE.write_text(json.dumps(s, indent=2), encoding="utf-8")


def _run(scr: str):
    subprocess.run([sys.executable, str(ROOT / scr)], cwd=str(ROOT),
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=240)


def _shadow_log_fades(rep, results):
    """Silently log detected fades (data-only: no Telegram/screenshot/⭐) so the backtest
    keeps validating the fade-ban. Plan is derived from scanner_d data only (cheap), with a
    +2R near target (the validated proxy). One entry per fade per day."""
    now = C.et_now()
    day = now.strftime("%Y-%m-%d")
    try:
        st = json.loads(SHADOW_STATE.read_text(encoding="utf-8")) if SHADOW_STATE.exists() else {}
    except Exception:
        st = {}
    logged = set(st.get("syms", [])) if st.get("date") == day else set()
    try:
        log = json.loads(SHADOW_LOG.read_text(encoding="utf-8")) if SHADOW_LOG.exists() else []
    except Exception:
        log = []
    added = 0
    for sym in rep.get("short_watch", []):
        if sym in logged:
            continue
        r = results.get(sym, {})
        last, vwap = r.get("last"), r.get("vwap")
        if not last or not vwap or last >= vwap:      # need a valid below-VWAP fade
            continue
        stop = round(vwap * 1.01, 2)
        rv = stop - last
        if rv <= 0:
            continue
        target = round(last - 2 * rv, 2)              # +2R near target
        log.insert(0, {
            "symbol": sym, "kind": "fade_short_watch", "label": "Fade-short",
            "et_time": now.strftime("%Y-%m-%d %H:%M ET"), "date": day,
            "flagged_ts": int(now.timestamp()), "dir": "short",
            "entry": round(last, 2), "stop": stop, "target": target,
            "rr": round((last - target) / rv, 1),
            "grade": None, "conviction": None, "crowned": False, "catalyst_lean": None,
            "data_only": True, "reviewed": False, "review": None,
        })
        logged.add(sym)
        added += 1
    if added:
        SHADOW_LOG.parent.mkdir(exist_ok=True)
        SHADOW_LOG.write_text(json.dumps(log[:60], indent=2), encoding="utf-8")
        SHADOW_STATE.write_text(json.dumps({"date": day, "syms": sorted(logged)}), encoding="utf-8")
    return added


def main():
    C.load_env()
    _run("scanner_a.py")
    _run("scanner_d.py")
    files = sorted((ROOT / "out").glob("scanner_d_*.json"))
    rep = json.loads(files[-1].read_text(encoding="utf-8")) if files else {}
    results = {r["symbol"]: r for r in rep.get("results", []) if r.get("ok")}

    today = C.et_now().strftime("%Y-%m-%d")
    state = _load_state()
    if state.get("date") == today:
        carded = state.get("carded")
        if carded is None:                          # migrate old {"seen":[...]} format
            carded = {k: {"conv": "?", "above_vwap": True, "recards": 1}
                      for k in state.get("seen", [])}
    else:
        carded = {}
    RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "?": 2}

    signals = []
    for kind, field in KIND_FIELD.items():
        for sym in rep.get(field, []):
            key = f"{sym}:{kind}"
            r = results.get(sym, {})
            prev = carded.get(key)
            # re-card a long that was carded BELOW vwap and has now RECLAIMED it (an
            # upgrade), once, if it wasn't already HIGH conviction.
            upgrade = bool(prev and kind == "long_pullback" and not prev.get("above_vwap")
                           and r.get("above_vwap") and RANK.get(prev.get("conv"), 2) < 2
                           and prev.get("recards", 0) < 1)
            if prev is not None and not upgrade:
                continue
            signals.append({"symbol": sym, "kind": kind, "label": KIND_LABEL[kind],
                            "gap_pct": r.get("gap_pct"), "cap_class": r.get("cap_class") or "?",
                            "upgrade": upgrade})
            # NB: NOT marked 'seen' here — tv_publish marks it once a real card is made,
            # so setups keep re-appearing until an actual card is produced (avoids losing
            # a setup that was still too thin on the 5m chart at detection time).

    OUTFILE.write_text(json.dumps(signals, indent=2), encoding="utf-8")
    if signals:                       # send the one-glance Watchboard overview first
        _run("tv_watchboard.py")
    _shadow_log_fades(rep, results)   # silently log fades (data-only) for the backtest
    _run("tv_review.py")              # fill in after-the-fact reviews for matured entries
    print(f"NEW signals: {len(signals)}")
    for s in signals:
        print(f"  {s['symbol']:6} {s['label']:16} gap {s['gap_pct']}%  {s['cap_class']}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
