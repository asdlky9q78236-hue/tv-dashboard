#!/usr/bin/env python3
"""LOCAL CLI: print the edge-tracker from analyses/backtest_log.jsonl.

Aggregates every reviewed setup (win/loss/scratch) by grade, conviction, type,
and ⭐-crown, showing win-rate + expectancy (R) so we can see whether the
strategy actually has an edge before optimising entries (Level 2 etc.).
"""
from __future__ import annotations

import sys

import common as C


def _line(label, s):
    wr = f"{s['win_rate']}%" if s["win_rate"] is not None else "—"
    exp = f"{s['expectancy_r']:+}R" if s["expectancy_r"] is not None else "—"
    print(f"  {label:18} n={s['n']:>3}  W/L/-={s['wins']}/{s['losses']}/{s['scratch']:<3}"
          f"  win={wr:>6}  exp={exp:>7}  gemMFE={s['avg_mfe']}%")


def main():
    recs = C.read_backtest_log()
    if not recs:
        print("geen backtest-data (analyses/backtest_log.jsonl bestaat nog niet / is leeg)")
        return
    st = C.backtest_stats(recs)
    ov = st["overall"]
    print(f"\n=== BACKTEST / EDGE ({ov['n']} afgeronde setups) ===")
    _line("OVERALL", ov)
    for key, title in [("grade", "Per grade"), ("conviction", "Per conviction"),
                       ("kind", "Per type"), ("crowned", "Kroon")]:
        print(f"\n{title}:")
        for k, s in st[key].items():
            if s["n"]:
                _line(str(k), s)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
