#!/usr/bin/env python3
"""
LOCAL stage 2 (not committed): compute the analysis + marker from LIVE
TradingView 5m bars (the same data you see on your chart), not Yahoo.

Usage: python tv_compute.py <tvbars.json> <symbol> <kind> <gap_pct> <cap_class>
  tvbars.json = the data_get_ohlcv response (has .bars with time/open/high/low/
  close/volume) OR a bare list of bars.
Writes out/tv_signal.json = {marker_time, marker_price, marker_text, caption,
  key_levels, session_from, session_to}.

Live-sensitive values (VWAP, 9 EMA, RSI, premarket H/L, today's HOD) come from
the TV bars; only prior-day high/low (settled history) come from yfinance.
"""
from __future__ import annotations

import os
import sys
import json
import datetime as dt
import email.utils
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import yfinance as yf

import common as C

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out" / "tv_signal.json"
LABELS = {"long_pullback": "VWAP-pullback", "long_extended": "Long (extended)",
          "fade_short_watch": "Fade-short"}


def _prior_day_levels(sym):
    try:
        d = yf.download(sym, period="1y", interval="1d", auto_adjust=False, progress=False)
        col = lambda x: x.iloc[:, 0] if hasattr(x, "columns") else x
        h, l, c = col(d["High"].dropna()), col(d["Low"].dropna()), col(d["Close"].dropna())
        if len(h) >= 2:
            sma200 = round(float(c.tail(200).mean()), 2) if len(c) >= 50 else None
            return {"pdh": round(float(h.iloc[-2]), 2), "pdl": round(float(l.iloc[-2]), 2),
                    "pdc": round(float(c.iloc[-2]), 2), "sma200": sma200}
    except Exception:
        pass
    return {}


CATALYST_KINDS = [
    ("earnings", ("earnings", "results", "beats", "misses", "revenue", "guidance", "quarter")),
    ("FDA/trial", ("fda", "approval", "phase", "trial", "clinical", "drug", "therapy", "designation")),
    ("rating", ("upgrade", "downgrade", "price target", "initiated", "analyst", "rating")),
    ("offering", ("offering", "dilution", "priced", "registered direct", "shelf", "warrant")),
    ("M&A", ("merger", "acquisition", "acquire", "buyout", "takeover")),
    ("deal", ("contract", "partnership", "agreement", "award", "collaboration")),
]


def _news_kind(title):
    t = title.lower()
    for kind, kws in CATALYST_KINDS:
        if any(k in t for k in kws):
            return kind
    return None


BEAR_WORDS = ("offering", "dilution", "dilutive", "priced", "registered direct", "shelf",
              "warrant", "downgrade", "cuts", "lowers", "misses", "disappoint", "investigation",
              "lawsuit", "halt", "delisting", "going concern", "bankruptcy", "fraud", "recall",
              "guidance cut", "short seller")
BULL_WORDS = ("upgrade", "raises", "raised", "beats", "approval", "approved", "wins", "awarded",
              "contract", "partnership", "acquire", "buyout", "takeover", "surges", "soars",
              "breakthrough", "clears", "positive results", "record")


def _catalyst_lean(title):
    """Directional read of a headline: 'bear' (offering/downgrade/miss…), 'bull'
    (upgrade/approval/beat…), or None (neutral). Bear checked first — dilution dominates."""
    t = title.lower()
    if any(w in t for w in BEAR_WORDS):
        return "bear"
    if any(w in t for w in BULL_WORDS):
        return "bull"
    return None


GENERIC_NEWS = ("sector update", "stocks to watch", "stocks moving", "premarket movers",
                "market update", "stocks rise", "stocks fall", "stocks decline", "stocks gain",
                "dow jones", "s&p 500", "nasdaq composite", "market close", "midday market",
                "what to watch", "stock market today", "futures", "movers:", "trending tickers",
                "market wrap", "wall street", "us stocks", "biggest movers")


def _catalyst(sym):
    """Newest per-ticker headline (< ~2 days) via real RSS: Yahoo Finance (ticker-specific)
    first, Google News fallback. Skips generic sector/market noise (not a real catalyst).
    Returns (display, lean) — display = 'headline [· type]', lean = 'bull'/'bear'/None —
    or (None, None) if no recent, genuine, ticker-specific news."""
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    feeds = [
        f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US",
        f"https://news.google.com/rss/search?q={sym}%20stock&hl=en-US&gl=US&ceid=US:en",
    ]
    for url in feeds:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=6).read()
            root = ET.fromstring(data)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                if not title or any(gm in title.lower() for gm in GENERIC_NEWS):
                    continue
                ts = None
                pub = item.findtext("pubDate")
                if pub:
                    try:
                        ts = email.utils.parsedate_to_datetime(pub).timestamp()
                    except Exception:
                        ts = None
                if ts is None or (now - ts) < 2 * 86400:
                    kind = _news_kind(title)
                    lean = _catalyst_lean(title)
                    return title[:78] + (f" · [{kind}]" if kind else ""), lean
        except Exception:
            continue
    return None, None


def _backtested_note(cap, gap, last, pdh):
    """Tag a setup with her published backtested edge where the criteria fit."""
    try:
        g = float(gap)
    except Exception:
        g = 0.0
    if cap in ("large", "mid") and g > 3 and last and last > 3 and pdh and last > pdh:
        return "📊 matcht haar backtested day-setup (~55% win: cap>$1B, RVOL>1.5, breekt gister-high)"
    return ""


def _time_note():
    """Her timing windows (first 2h after open; 10:30-11 golden dip window)."""
    t = C.et_now().time()
    if dt.time(10, 30) <= t < dt.time(11, 0):
        return " ⏰ golden dip-window (10:30-11 ET)."
    if dt.time(9, 30) <= t < dt.time(11, 30):
        return " ⏰ binnen haar power-window (eerste 2u)."
    return ""


def _enrich(tvbars):
    """Today's regular-session bars with VWAP/9EMA/RSI + premarket H/L, from TV bars."""
    today = C.et_now().date()
    pre_h, pre_l, reg = [], [], []
    for b in tvbars:
        t = dt.datetime.fromtimestamp(b["time"], C.ET)
        if t.date() != today:
            continue
        if t.time() < dt.time(9, 30):
            if t.time() >= dt.time(4, 0):
                pre_h.append(b["high"]); pre_l.append(b["low"])
        else:
            reg.append(b)
    pmh = round(max(pre_h), 2) if pre_h else None
    pml = round(min(pre_l), 2) if pre_l else None
    if not reg:
        return [], pmh, pml
    closes = [b["close"] for b in reg]
    # 9 EMA
    ema, k, e = [], 2 / 10, closes[0]
    for c in closes:
        e = c * k + e * (1 - k)
        ema.append(e)
    # RSI(14), Wilder
    rsis, ag, al, prev = [], 0.0, 0.0, closes[0]
    for i, c in enumerate(closes):
        if i == 0:
            rsis.append(50.0); continue
        ch = c - prev; prev = c
        g, ls = max(ch, 0), max(-ch, 0)
        if i <= 14:
            ag = (ag * (i - 1) + g) / i; al = (al * (i - 1) + ls) / i
        else:
            ag = (ag * 13 + g) / 14; al = (al * 13 + ls) / 14
        rs = ag / al if al > 0 else 999
        rsis.append(100 - 100 / (1 + rs))
    bars, cum_pv, cum_v = [], 0.0, 0.0
    for i, b in enumerate(reg):
        typ = (b["high"] + b["low"] + b["close"]) / 3
        cum_pv += typ * b["volume"]; cum_v += b["volume"]
        bars.append({"time": b["time"], "o": b["open"], "h": b["high"], "l": b["low"],
                     "c": b["close"], "v": b["volume"],
                     "vwap": round(cum_pv / cum_v if cum_v > 0 else b["close"], 4),
                     "ema9": round(ema[i], 4), "rsi": round(rsis[i], 1)})
    return bars, pmh, pml


def _vol_trend(bars):
    if len(bars) < 6:
        return "volume onbekend"
    recent = sum(b["v"] for b in bars[-3:]) / 3
    prior = sum(b["v"] for b in bars[-6:-3]) / 3
    if prior <= 0:
        return "volume onbekend"
    rr = recent / prior
    return "stijgend volume" if rr >= 1.2 else ("afnemend volume" if rr <= 0.8 else "stabiel volume")


def _reclaim_trigger(kind, vwap, tv1):
    """1-min timing vs the authoritative session VWAP (from the 5m card). tv1 = raw
    1-min bars (time/open/high/low/close/volume). We compare recent 1m action to that
    single VWAP value — no partial-window VWAP recompute. Beslis op 5-min, tik af op 1-min."""
    today = C.et_now().date()
    b = []
    for x in tv1:
        t = dt.datetime.fromtimestamp(x["time"], C.ET)
        if t.date() == today and t.time() >= dt.time(9, 30):
            b.append(x)
    if len(b) < 5:
        return None
    recent = b[-6:]
    last = b[-1]
    win = b[-15:]
    avg_v = sum(x["volume"] for x in win) / len(win)
    vol_ok = last["volume"] >= avg_v
    if kind == "long_pullback":
        dipped = any(x["low"] <= vwap for x in recent)         # pulled back to/under VWAP
        if last["close"] >= vwap and dipped and vol_ok:
            return "✅ 1m-TRIGGER: reclaim-candle sluit boven VWAP mét volume — instap actief."
        if last["close"] >= vwap:
            return "🟡 1m: boven VWAP maar volume mager — wacht op sterke reclaim-candle."
        return "⏳ 1m: nog onder VWAP — wacht tot een candle erboven sluit (reclaim)."
    if kind == "fade_short_watch":
        tested = any(x["high"] >= vwap for x in recent)        # tested VWAP from below
        if last["close"] < vwap and tested and vol_ok:
            return "✅ 1m-TRIGGER: candle verliest VWAP mét volume — short actief."
        if last["close"] < vwap:
            return "🟡 1m: onder VWAP maar volume mager — wacht op sterke afwijzing."
        return "⏳ 1m: nog boven/op VWAP — nog geen short-trigger."
    return None


def _liquidity(bars):
    """Avg $-volume per 5m bar as a liquidity proxy → (dollar_vol, tier). She avoids
    friction by SELECTING liquid, high-volume names + limit orders (not by modelling
    slippage). Thin names = slippage + hard to exit."""
    if not bars:
        return 0.0, "onbekend"
    dvol = sum(b["v"] * b["c"] for b in bars) / len(bars)
    tier = "liquide" if dvol >= 1_000_000 else ("matig" if dvol >= 100_000 else "dun")
    return dvol, tier


def _marker(kind, bars, gap, cap, pmh, pml, lv):
    b = bars[-1]
    last, vwap, ema9, rsi = b["c"], b["vwap"], b["ema9"], b["rsi"]
    voltrend = _vol_trend(bars)
    above = "boven" if last >= vwap else "onder"
    ema_rel = "boven 9 EMA" if last >= ema9 else "onder 9 EMA"
    rsi_txt = f"RSI {rsi:.0f}" if len(bars) >= 10 else "RSI n.v.t. (te weinig bars)"
    pdh, pdl, pdc = lv.get("pdh"), lv.get("pdl"), lv.get("pdc")
    tn = _time_note()
    # 3 most decision-relevant levels (R/G line + upside & downside targets) — keeps
    # the chart-draw count low for speed.
    klv = [{"price": p, "label": lab} for p, lab in
           ((pdc, "Rood/groen-lijn (gisteren slot)"), (pmh, "Premarket high"),
            (pdl, "Vorige dag low"), (pml, "Premarket low"),
            (pdh, "Vorige dag high")) if p][:3]

    abbr = {"Premarket high": "PMH", "Premarket low": "PML",
            "Rood/groen-lijn (gisteren slot)": "R/G", "Vorige dag high": "PDH",
            "Vorige dag low": "PDL"}
    keyline = " · ".join(f"{abbr.get(k['label'], k['label'])} ${k['price']:.2f}" for k in klv) or "—"

    if kind == "long_pullback":
        held = [x for x in bars if x["l"] <= x["vwap"] * 1.006 and x["c"] >= x["vwap"]]
        mb = held[-1] if held else b
        # her level-based "singles": sell into the nearest overhead level — HOD first,
        # then premarket high, then prior-day high (not an invented R-multiple).
        hod = round(max(x["h"] for x in bars), 2)
        cand = sorted((p, lbl) for p, lbl in ((hod, "HOD"), (pmh, "PMH"), (pdh, "PDH"))
                      if p and p > last * 1.002)
        target, tlabel = (cand[0][0], cand[0][1]) if cand else (None, "")
        stop = round(min(min(x["l"] for x in bars[-4:]), mb["vwap"] * 0.99), 2)
        rr = round((target - last) / max(last - stop, 0.01), 1) if target else None
        tgt = f" · DOEL ${target:.2f} ({tlabel}) · R:R ~1:{rr}" if target and rr and 0.2 <= rr <= 10 else ""
        findings = (
            f"STATUS: Instap-zone · {above} VWAP\n"
            f"NU: {cap}-cap +{gap}%, dip naar VWAP ${mb['vwap']:.2f} en houdt; {ema_rel} "
            f"(${ema9:.2f}), {rsi_txt}, {voltrend}.\n"
            f"ACTIE: koop de dip-zone bij VWAP; scale-in 2-4×; bevestiging = reclaim+hold + volume. "
            f"Niet de top najagen.\n"
            f"STOP ${stop} (structuur, níet op VWAP){tgt}\n"
            f"KEY: {keyline}{tn}")
        plan = {"entry": round(last, 2), "stop": stop, "target": target,
                "rr": rr if (target and rr and 0.2 <= rr <= 10) else None,
                "rr_raw": rr, "dir": "long"}
        return mb["time"], round(mb["l"], 2), "Pullback → VWAP houdt ▲", findings, klv, plan

    if kind == "fade_short_watch":
        touch = [x for x in bars if x["h"] >= x["vwap"]]
        mb = touch[-1] if touch else bars[0]
        target = pdl if (pdl and pdl < last) else None
        stop = round(max(mb["h"], vwap) * 1.01, 2)
        rr = round((last - target) / max(stop - last, 0.01), 1) if target else None
        tgt = f" · DOEL ${target:.2f} · R:R ~1:{rr}" if target and rr and 0.2 <= rr <= 10 else ""
        findings = (
            f"STATUS: {above} VWAP · verkopers-bias\n"
            f"NU: {cap}-cap +{gap}%, tikte VWAP ${mb['vwap']:.2f} → afgewezen; {ema_rel}, "
            f"{rsi_txt}, {voltrend}.\n"
            f"ACTIE: short-bias zolang onder VWAP; herhaald falen reclaim = zwak. "
            f"(Zij is long-biased; short = risico.)\n"
            f"STOP ${stop}{tgt}\n"
            f"KEY: {keyline}{tn}")
        plan = {"entry": round(last, 2), "stop": stop, "target": target,
                "rr": rr if (target and rr and 0.2 <= rr <= 10) else None,
                "rr_raw": rr, "dir": "short"}
        return mb["time"], round(mb["h"], 2), "VWAP verloren ▼", findings, klv, plan

    mb = max(bars, key=lambda x: x["h"])
    pmh_txt = f" of PMH ${pmh:.2f}" if pmh else ""
    findings = (
        f"STATUS: Extended · niet najagen\n"
        f"NU: {cap}-cap op dagtop ${mb['h']:.2f}, ver boven VWAP ${vwap:.2f}; {rsi_txt}, {voltrend}.\n"
        f"ACTIE: WACHT op pullback naar VWAP ${vwap:.2f}{pmh_txt}; najagen = slechte R:R.\n"
        f"KEY: {keyline}{tn}")
    plan = {"entry": None, "stop": None, "target": None, "rr": None, "dir": "long"}
    return mb["time"], round(mb["h"], 2), "Extended ▲ (niet najagen)", findings, klv, plan


def _grade(kind, bars, plan, gap, cap, trigger, sma200=None):
    """Score a setup against her A-criteria checklist → (grade, passed, total, reasons).
    A = one of the 1-2/day she'd actually take; B = decent; C = skip in real trading."""
    b = bars[-1]
    last, vwap, ema9 = b["c"], b["vwap"], b["ema9"]
    voltrend = _vol_trend(bars)
    try:
        g = float(gap)
    except Exception:
        g = 0.0
    rr = plan.get("rr_raw")
    if kind == "long_pullback":
        struct_lbl, struct_ok = "houdt VWAP + boven 9 EMA", last >= vwap * 0.995 and last >= ema9
        daily = bool(sma200) and last >= sma200
    elif kind == "fade_short_watch":
        struct_lbl, struct_ok = "onder VWAP (afgewezen)", last < vwap
        daily = bool(sma200) and last < sma200
    else:
        struct_lbl, struct_ok, daily = "playbook", False, False
    checks = [
        (struct_lbl, struct_ok),
        ("R:R ≥ 1.5", bool(rr) and rr >= 1.5),
        ("1m-trigger ✅", bool(trigger) and trigger.startswith("✅")),
        ("volume mee", voltrend == "stijgend volume"),
        ("gap 8-80% (sweet spot)", 8 <= g <= 80),
        ("small-cap", cap == "small"),
        ("power-window", bool(_time_note())),
        ("daily-bias (200SMA)", daily),
    ]
    passed = sum(1 for _, ok in checks if ok)
    grade = "A" if passed >= 6 else ("B" if passed >= 4 else "C")
    return grade, passed, len(checks), [name for name, ok in checks if ok]


def _skeptic(kind, bars, gap, cap, plan, trigger, catalyst=None, cat_lean=None, liq_tier=None):
    """Blind 'second brain': list reasons to SKIP (the bear case), independent of the
    bullish grade. Defaults to skepticism (her Codex role: find traps & priced-in moves)."""
    b = bars[-1]
    last, vwap, rsi = b["c"], b["vwap"], b["rsi"]
    enough = len(bars) >= 10
    try:
        g = float(gap)
    except Exception:
        g = 0.0
    flags = []
    if kind == "long_pullback":
        if last > vwap * 1.08:
            flags.append("ver boven VWAP → late instap, slechte R:R")
        if enough and rsi >= 78:
            flags.append(f"RSI {rsi:.0f} overgekocht → uitputtingsrisico")
    if kind == "fade_short_watch":
        tgt = plan.get("target")
        if tgt and last and (last - tgt) / last < 0.04:
            flags.append("al dicht bij doel/steun → weinig ruimte omlaag")
        if enough and rsi <= 22:
            flags.append(f"RSI {rsi:.0f} oververkocht → bounce-risico tegen de short")
    if _vol_trend(bars) == "afnemend volume":
        flags.append("volume droogt op → geen overtuiging")
    if g > 100:
        flags.append(f"parabolisch (+{g:.0f}%) → scherpe reversal-kans")
    if trigger and not trigger.startswith("✅"):
        flags.append("1m-timing nog niet bevestigd")
    if liq_tier == "dun":
        flags.append("dun/illiquide → slippage & moeilijk uitstappen")
    if not catalyst:
        flags.append("geen duidelijke catalyst gevonden")
    elif cat_lean == "bear" and kind == "long_pullback":
        flags.append("catalyst is bearish (offering/downgrade?) — tegen je long")
    elif cat_lean == "bull" and kind == "fade_short_watch":
        flags.append("catalyst is bullish — tegen je short/fade")
    entry, stop = plan.get("entry"), plan.get("stop")
    if entry and stop and abs(entry - stop) / entry > 0.05:
        flags.append(f"stop ver ({abs(entry - stop) / entry * 100:.0f}%) → grote loss bij mis")
    return flags


def main():
    tvfile, symbol, kind, gap, cap = sys.argv[1:6]
    raw = json.loads(Path(tvfile).read_text(encoding="utf-8"))
    tvbars = raw["bars"] if isinstance(raw, dict) else raw
    bars, pmh, pml = _enrich(tvbars)
    if len(bars) < 3:
        print(f"te weinig 5m-bars ({len(bars)}<3) — nog te vroeg, wachten")
        OUT.write_text("{}", encoding="utf-8"); return
    # optional 1-min bars (argv[6]) → reclaim/loss trigger (timing confirmation)
    trigger = None
    if len(sys.argv) > 6:
        try:
            raw1 = json.loads(Path(sys.argv[6]).read_text(encoding="utf-8"))
            tv1 = raw1["bars"] if isinstance(raw1, dict) else raw1
            trigger = _reclaim_trigger(kind, bars[-1]["vwap"], tv1)
        except Exception:
            trigger = None
    lv = _prior_day_levels(symbol)
    cat, cat_lean = _catalyst(symbol)
    t, price, text, findings, klv, plan = _marker(kind, bars, gap, cap, pmh, pml, lv)
    if trigger:
        findings += f"\n{trigger}"
    grade, gp, gt, oks = _grade(kind, bars, plan, gap, cap, trigger, lv.get("sma200"))
    gmsg = {"A": "🎯 GRADE A — dit zou ik nemen als ik écht handelde.",
            "B": "GRADE B — degelijk, maar niet mijn eerste keuze.",
            "C": "GRADE C — zwak; bij echt traden zou ik 'm overslaan."}[grade]
    grade_line = f"{gmsg} ({gp}/{gt})"
    # second brain: skeptic + conviction (agreement between bullish grade and bear case)
    dvol, liq_tier = _liquidity(bars)
    concerns = _skeptic(kind, bars, gap, cap, plan, trigger, cat, cat_lean, liq_tier)
    n = len(concerns)
    if grade == "A" and n == 0:
        conv, conv_full = "HIGH", "🟢 HIGH — beide breinen eens: nemen."
    elif grade in ("A", "B") and n <= 1:
        conv, conv_full = "MEDIUM", "🟡 MEDIUM — verdeeld: klein/voorzichtig."
    else:
        conv, conv_full = "LOW", "🔴 LOW — sceptisch brein tegen: overslaan."
    if (cat_lean == "bear" and kind == "long_pullback") or (cat_lean == "bull" and kind == "fade_short_watch"):
        conv, conv_full = "LOW", "🔴 LOW — catalyst tegen de richting (bijv. dilutie/offering): overslaan."
    skeptic_line = "🧠 Sceptisch brein: " + ("; ".join(concerns) if concerns else "geen grote bezwaren.")
    # 1 backtested context · 2 catalyst · 3 scale-out plan (1/3 +1R, +2R, trail 21-EMA)
    lean_mark = {"bull": " — 🟢 bullish", "bear": " — 🔴 bearish"}.get(cat_lean, "")
    cat_line = f"📰 Catalyst: {cat}{lean_mark}" if cat else "📰 Catalyst: geen recent nieuws gevonden ⚠️"
    bt_note = _backtested_note(cap, gap, bars[-1]["c"], lv.get("pdh"))
    entry, stop, dirn = plan.get("entry"), plan.get("stop"), plan.get("dir")
    scale_line = size_line = ""
    if entry and stop and abs(entry - stop) > 0:
        rv = abs(entry - stop)
        scale_line = ("🎯 Exit (haar 'singles'): 1/3 in doel/HOD · trim bij 9-EMA-extensie · "
                      "rest breakeven-stop — sell into strength, niet chasen")
        acct = float(os.environ.get("ACCOUNT_SIZE", "25000") or 25000)
        risk_d = acct * 0.01
        shares = int(risk_d / rv)
        size_line = (f"💰 Sizing (1% v/${acct:,.0f} = ${risk_d:,.0f} risico): ~{shares:,} aandelen "
                     f"≈ ${shares * entry:,.0f} positie")
    liq_line = f"💧 Liquiditeit ~${dvol / 1000:,.0f}k/5m ({liq_tier}) · limit-order, niet chasen"
    watch_line = ("⚠️ WATCH-ONLY: zij is long-biased; small-cap shorten = gevorderd/risico — geen ⭐."
                  if kind == "fade_short_watch" else "")
    extra = "\n".join(x for x in (cat_line, bt_note, liq_line, size_line, scale_line, watch_line) if x)
    OUT.write_text(json.dumps({
        "symbol": symbol, "kind": kind, "label": LABELS.get(kind, kind),
        "marker_time": t, "marker_price": price, "marker_text": text,
        "key_levels": klv, "plan": plan, "trigger": trigger,
        "above_vwap": bars[-1]["c"] >= bars[-1]["vwap"],
        "catalyst": cat, "catalyst_lean": cat_lean,
        "grade": grade, "grade_score": f"{gp}/{gt}", "grade_reasons": oks,
        "conviction": conv, "concerns": concerns,
        "caption": (f"📊 {symbol} — {LABELS.get(kind, kind)} (live TV)\n"
                    f"{grade_line}\nCONVICTION: {conv_full}\n{findings}\n{extra}\n{skeptic_line}"),
        "session_from": bars[0]["time"], "session_to": bars[-1]["time"],
    }, indent=2), encoding="utf-8")
    print(f"{symbol} {LABELS.get(kind,kind)} mark@{price} (from {len(bars)} live TV bars)")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
