"""
Shared helpers for the TradingView/HumbledTrader scanner pipeline.

Data layer is yfinance-only so the scripts run anywhere (incl. cloud
/schedule routines) without depending on the local TradingView Desktop.
Yahoo's v7 quote endpoint is locked down (Unauthorized), so we rely on
yfinance batch download which handles the crumb/cookie auth for us.
"""
from __future__ import annotations

import os
import sys
import json
import time
import datetime as dt
from pathlib import Path
from typing import Iterable

import yfinance as yf

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    ET = dt.timezone(dt.timedelta(hours=-4))  # EDT fallback

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)


# --------------------------------------------------------------------------
# .env loading (no external dependency)
# --------------------------------------------------------------------------
def load_env(path: Path | None = None) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (no overwrite)."""
    path = path or (ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------
def et_now() -> dt.datetime:
    return dt.datetime.now(ET)


def wb_grade(r: dict) -> str:
    """Lightweight PROVISIONAL grade (A/B/C) for the Watchboard, from scanner_d data
    only (gap, cap, structure, overextension, power-window). The full grade on the
    card refines this with R:R + the 1-min trigger, so treat this as a first read."""
    kind = r.get("kind")
    above = r.get("above_vwap")
    try:
        g = float(r.get("gap_pct") or 0)
    except Exception:
        g = 0.0
    t = et_now().time()
    in_window = dt.time(9, 30) <= t < dt.time(11, 30)
    aligned = (kind == "long_pullback" and above) or (kind == "fade_short_watch" and not above)
    checks = [
        aligned,
        8 <= g <= 80,
        r.get("cap_class") == "small",
        not r.get("overextended"),
        in_window,
    ]
    passed = sum(1 for c in checks if c)
    return "A" if passed >= 4 else ("B" if passed >= 2 else "C")


def is_premarket(now: dt.datetime | None = None) -> bool:
    now = now or et_now()
    t = now.time()
    return dt.time(4, 0) <= t < dt.time(9, 30)


def today_str(now: dt.datetime | None = None) -> str:
    return (now or et_now()).strftime("%Y%m%d")


# --------------------------------------------------------------------------
# Market data
# --------------------------------------------------------------------------
def get_daily_stats(symbols: list[str]) -> dict[str, dict]:
    """
    {symbol: {prev_close, avg_vol}} from one daily download.

    prev_close = last completed daily close.
    avg_vol    = mean daily volume over the window (liquidity proxy used
                 instead of premarket volume, which Yahoo's free feed does
                 not expose for extended hours).
    """
    out: dict[str, dict] = {}
    df = yf.download(symbols, period="30d", interval="1d",
                     auto_adjust=False, progress=False, threads=True)
    if df is None or df.empty:
        return out
    close, vol = df["Close"], df["Volume"]
    cols = close.columns if hasattr(close, "columns") else None

    def _stats(c, v):
        c, v = c.dropna(), v.dropna()
        if not len(c):
            return None
        # RVOL: most recent session volume vs the 14 sessions before it
        rvol = 0.0
        if len(v) >= 2:
            last_vol = float(v.iloc[-1])
            base = v.iloc[-15:-1] if len(v) > 14 else v.iloc[:-1]
            avg14 = float(base.mean()) if len(base) else 0.0
            rvol = round(last_vol / avg14, 2) if avg14 > 0 else 0.0
        return {"prev_close": float(c.iloc[-1]),
                "avg_vol": int(v.mean()) if len(v) else 0,
                "rvol": rvol}

    if cols is not None:
        for sym in cols:
            s = _stats(close[sym], vol[sym])
            if s:
                out[sym] = s
    else:
        s = _stats(close, vol)
        if s:
            out[symbols[0]] = s
    return out


def get_premarket_snapshot(symbols: list[str]) -> dict[str, dict]:
    """
    Return {symbol: {last, premarket_volume, source}} using prepost 1m bars.

    'last' is the most recent premarket close if available, otherwise the
    latest intraday close (so the scanner still works when run outside the
    premarket window, e.g. for testing).
    """
    res: dict[str, dict] = {}
    df = yf.download(symbols, period="1d", interval="1m", prepost=True,
                     auto_adjust=False, progress=False, threads=True,
                     group_by="ticker")
    if df is None or df.empty:
        return res
    multi = len(symbols) > 1 and hasattr(df.columns, "levels")
    for sym in symbols:
        try:
            sub = df[sym] if multi else df
        except KeyError:
            continue
        sub = sub.dropna(subset=["Close"])
        if sub.empty:
            continue
        idx = sub.index
        try:
            idx_et = idx.tz_convert(ET)
        except Exception:
            idx_et = idx
        open_t = dt.time(9, 30)
        pre_mask = [t.time() < open_t for t in idx_et]
        pre = sub[pre_mask]
        if not pre.empty:
            last = float(pre["Close"].iloc[-1])
            vol = int(pre["Volume"].sum())
            source = "premarket"
        else:
            last = float(sub["Close"].iloc[-1])
            vol = int(sub["Volume"].sum())
            source = "intraday_fallback"
        res[sym] = {"last": last, "premarket_volume": vol, "source": source}
    return res


def get_market_caps(symbols: list[str]) -> dict[str, float]:
    """
    {symbol: market_cap_usd} via yfinance fast_info (threaded, best-effort).
    Used to apply HumbledTrader's large-cap (>$10B) / small-cap screen.
    """
    from concurrent.futures import ThreadPoolExecutor

    def one(sym):
        try:
            # fast_info.get("market_cap") returns None; use attribute access
            mc = yf.Ticker(sym).fast_info.market_cap
            return sym, float(mc) if mc else None
        except Exception:
            return sym, None

    out: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for sym, mc in ex.map(one, symbols):
            if mc:
                out[sym] = mc
    return out


def get_news(symbol: str, limit: int = 2) -> list[dict]:
    """Free-source headlines via yfinance (Yahoo). Best-effort, never raises."""
    try:
        items = yf.Ticker(symbol).news or []
    except Exception:
        return []
    out = []
    for it in items[:limit]:
        content = it.get("content", it)  # yfinance schema varies by version
        title = content.get("title") or it.get("title")
        pub = (content.get("provider", {}) or {}).get("displayName") \
            or it.get("publisher")
        link = ((content.get("canonicalUrl", {}) or {}).get("url")
                or it.get("link"))
        if title:
            out.append({"title": title, "publisher": pub, "link": link})
    return out


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------
def send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message via the Telegram Bot API. Returns True on success."""
    import urllib.request
    import urllib.parse

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("[telegram] no token/chat_id set; skipping send", file=sys.stderr)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": "true",
    }).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=20) as r:
            ok = json.loads(r.read().decode()).get("ok", False)
            if not ok:
                print("[telegram] API returned ok=false", file=sys.stderr)
            return ok
    except Exception as e:
        print(f"[telegram] send failed: {e}", file=sys.stderr)
        return False


def write_json(name: str, payload: dict) -> Path:
    path = OUT / name
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path
