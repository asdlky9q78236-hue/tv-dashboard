#!/usr/bin/env python3
"""
LOCAL (not committed): send a compact WATCHBOARD to Telegram — all current
setups in one glance. Reads the latest scanner_d output. Run once per cycle
(the driver calls it before the individual decision cards).
"""
from __future__ import annotations

import sys
import json
import glob

import common as C

ROOT = C.ROOT if hasattr(C, "ROOT") else None
import pathlib
ROOT = pathlib.Path(__file__).resolve().parent

ORDER = [("long_pullback", "🟢 Pullback"), ("fade_short_watch", "🔴 Fade-short"),
         ("long_extended", "🔵 Extended"), ("neutral", "⚪ Neutraal"),
         ("premarket", "⏳ Pre-open")]


def main():
    C.load_env()
    files = sorted(glob.glob(str(ROOT / "out" / "scanner_d_*.json")))
    if not files:
        print("no scanner_d output"); return
    d = json.loads(open(files[-1], encoding="utf-8").read())
    groups = {}
    for r in d.get("results", []):
        if not r.get("ok"):
            continue
        rel = "&gt;VWAP" if r.get("above_vwap") else "&lt;VWAP"
        cap = r.get("cap_class")
        gap = r.get("gap_pct")
        kind = r.get("kind", "neutral")
        gr = C.wb_grade(r) if kind in ("long_pullback", "fade_short_watch") else ""
        if gr == "C":                      # overtrading brake: hide C-grade setups
            continue
        badge = f"<b>[{gr}]</b> " if gr else ""
        item = f"{badge}{r['symbol']} <i>{rel}"
        if gap is not None:
            item += f" +{gap}%"
        item += "</i>"
        groups.setdefault(kind, []).append(item)
    if not groups:
        print("no setups"); return
    now = C.et_now()
    m = d.get("market", {})
    lines = [f"<b>🖥️ Watchboard</b> · <i>{now:%H:%M} ET</i>"]
    if m.get("pct") is not None:
        lines.append(f"Markt: SPY {m['pct']:+}% ({m.get('tone', '?')})")
    for kind, label in ORDER:
        if groups.get(kind):
            lines.append(f"<b>{label}:</b> " + " · ".join(groups[kind]))
    if groups.get("long_pullback") or groups.get("fade_short_watch"):
        lines.append("<i>[A]=zou ik nemen · [B]=degelijk · C verborgen · max 2 trades/dag (haar regel)</i>")
    text = "\n".join(lines)
    ok = C.send_telegram(text)
    print(f"watchboard sent: {ok}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
