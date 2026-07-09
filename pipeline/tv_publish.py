#!/usr/bin/env python3
"""
LOCAL helper (not committed): publish one TradingView analysis.
  - sends the screenshot + caption to Telegram (sendPhoto)
  - copies the image into analyses/ and prepends an entry to analyses/log.json
    (the committed log the dashboard renders).

Usage: python tv_publish.py <image_png> <symbol> <kind> <label> <caption_file>
(caption is read from a file to avoid shell-escaping issues.)
"""
from __future__ import annotations

import sys
import os
import json
import shutil
from pathlib import Path

import requests

import common as C

ROOT = Path(__file__).resolve().parent
ANALYSES = ROOT.parent / "analyses"
LOG = ANALYSES / "log.json"
MAX_ENTRIES = 16


def main():
    img, symbol, kind, label, caption_file = sys.argv[1:6]
    caption = Path(caption_file).read_text(encoding="utf-8").strip()
    C.load_env()
    now = C.et_now()
    day = now.strftime("%Y-%m-%d")

    # read computed signal once (grade + plan)
    sig = {}
    try:
        sig = json.loads((ROOT / "out" / "tv_signal.json").read_text(encoding="utf-8"))
    except Exception:
        sig = {}
    plan = sig.get("plan", {}) or {}
    grade = sig.get("grade")
    conviction = sig.get("conviction")

    # re-card detection: was this key already carded today? → upgrade banner
    sp = ROOT / "out" / "tv_analyzed_state.json"
    is_recard = False
    try:
        _st = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
        if _st.get("date") == day:
            _c = _st.get("carded") or {k: 1 for k in _st.get("seen", [])}
            is_recard = f"{symbol}:{kind}" in _c
    except Exception:
        pass

    # day-cap: crown at most 2 "A-setups van de dag" (the 1-2 she'd actually take)
    crowned = False
    ap = ROOT / "out" / "a_state.json"
    try:
        ast = json.loads(ap.read_text(encoding="utf-8")) if ap.exists() else {}
    except Exception:
        ast = {}
    a_syms = ast.get("syms", []) if ast.get("date") == day else []
    if grade == "A" and conviction != "LOW" and kind == "long_pullback":
        if symbol in a_syms:
            crowned = True
        elif len(a_syms) < 2:
            a_syms.append(symbol); crowned = True
            ap.write_text(json.dumps({"date": day, "syms": a_syms}), encoding="utf-8")
    if crowned:
        caption = f"⭐ A-SETUP VAN DE DAG (#{a_syms.index(symbol) + 1}/2) ⭐\n\n{caption}"
    elif grade == "A":
        caption = f"ℹ️ A-kwaliteit — dag-top-2 al vergeven, dus vandaag als extra test.\n\n{caption}"
    if is_recard:
        caption = f"🔁 UPGRADE (reclaim) — eerder als zwak gemarkeerd, nu sterker.\n\n{caption}"

    if len(caption) > 1024:                     # Telegram sendPhoto caption hard limit
        caption = caption[:1021].rstrip() + "…"

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    tg_ok = False
    if token and chat:
        try:
            with open(img, "rb") as f:
                r = requests.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    data={"chat_id": chat, "caption": caption},
                    files={"photo": f}, timeout=30)
            tg_ok = bool(r.ok and r.json().get("ok"))
        except Exception as e:
            print(f"[tg] send failed: {e}")
    else:
        print("[tg] no token/chat; skipping telegram")

    ANALYSES.mkdir(exist_ok=True)
    fname = f"{symbol}_{now:%Y%m%d_%H%M%S}.png"
    shutil.copy(img, ANALYSES / fname)
    try:
        log = json.loads(LOG.read_text(encoding="utf-8"))
    except Exception:
        log = []
    log.insert(0, {
        "symbol": symbol, "kind": kind, "label": label,
        "et_time": now.strftime("%Y-%m-%d %H:%M ET"),
        "date": now.strftime("%Y-%m-%d"), "flagged_ts": int(now.timestamp()),
        "dir": plan.get("dir"), "entry": plan.get("entry"), "stop": plan.get("stop"),
        "target": plan.get("target"), "rr": plan.get("rr"),
        "grade": grade, "crowned": crowned, "conviction": conviction,
        "catalyst_lean": sig.get("catalyst_lean"),
        "findings": caption, "image": f"analyses/{fname}",
        "reviewed": False, "review": None,
    })
    LOG.write_text(json.dumps(log[:MAX_ENTRIES], indent=2), encoding="utf-8")

    # dedup: record this setup as carded (conviction + vwap side + re-card count) so
    # tv_analyze can re-emit ONE upgrade if a below-VWAP long later reclaims VWAP.
    try:
        st = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
        if st.get("date") == day:
            carded = st.get("carded") or {k: {"conv": "?", "above_vwap": True, "recards": 1}
                                          for k in st.get("seen", [])}
        else:
            carded = {}
        key = f"{symbol}:{kind}"
        prev = carded.get(key)
        carded[key] = {"conv": conviction, "above_vwap": bool(sig.get("above_vwap")),
                       "recards": (prev.get("recards", 0) + 1) if isinstance(prev, dict) else 0}
        sp.write_text(json.dumps({"date": day, "carded": carded}), encoding="utf-8")
    except Exception:
        pass
    print(f"published {symbol} [{label}] telegram_ok={tg_ok} -> analyses/{fname}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
