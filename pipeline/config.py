"""Tunable parameters for the scanner pipeline.

Defaults below are overridden by rules.json (the declarative source of truth)
when that file is present — edit rules.json to tune without touching code.
"""
import json
from pathlib import Path

# --- Defaults (used when rules.json is missing a key) ---
# HumbledTrader-style gap screener. Mode picks her large-cap vs small-cap rules.
SCAN_MODE = "largecap"   # largecap | smallcap | any
UNIVERSE_FILE = None     # default universe filename (e.g. "sp500.txt")
GAP_MIN_PCT = 3.0        # large-cap premarket gap floor (her ">= 3%")
GAP_MIN_PCT_SMALLCAP = 10.0  # small-cap gap floor (her ">= 10%")
PRICE_MIN = 1.0          # her floor: avoid sub-$1 stocks
# Yahoo's free feed gives no extended-hours volume, so we gate on average
# daily volume as a liquidity proxy instead of raw premarket volume.
AVG_VOL_MIN = 300_000
RVOL_MIN = 1.0           # her Finviz "relative volume > 1"
MARKET_CAP_MIN_USD = 10_000_000_000   # large-cap = > $10B
SMALLCAP_MAX_USD = 800_000_000        # her small-cap cutoff (< $800M)
TOP_N = 20               # how many candidates to report

# --- Scanner B: Trend Join Long tickers ---
SCANNER_B_TICKERS = ["AMD", "NVDA", "MU"]
SMA_LEN = 200
SCANNER_B_RVOL_MIN = 1.5

# --- Scanner C: crypto-pair daily setup scanner ---
CRYPTO_SMA_LEN = 200       # daily trend filter
CRYPTO_DON_LEN = 20        # Donchian breakout lookback
CRYPTO_LOOKBACK_DAYS = 365 # window for setup-frequency stats
CRYPTO_FWD_DAYS = 10       # forward horizon for reliability check
CRYPTO_REGIME = "BTC-USD"  # market regime gate (above its 200-SMA)
# Liquid USD pairs (Yahoo format). Newer/illiquid pairs may have short history.
CRYPTO_UNIVERSE = [
    "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD", "ADA-USD",
    "AVAX-USD", "DOGE-USD", "LINK-USD", "DOT-USD", "LTC-USD", "BCH-USD",
    "ATOM-USD", "NEAR-USD", "APT-USD", "ARB-USD", "OP-USD", "INJ-USD",
    "SUI-USD", "TIA-USD", "SEI-USD", "FET-USD", "RNDR-USD", "AAVE-USD",
    "UNI-USD", "FIL-USD", "ICP-USD", "HBAR-USD", "ALGO-USD", "XLM-USD",
    "VET-USD", "GRT-USD", "MKR-USD", "RUNE-USD", "IMX-USD", "STX-USD",
]

# --- Overlay rules.json if present ---
_rules_path = Path(__file__).resolve().parent / "rules.json"
if _rules_path.exists():
    try:
        _r = json.loads(_rules_path.read_text(encoding="utf-8"))
        _a = _r.get("scanner_a", {})
        SCAN_MODE = _a.get("mode", SCAN_MODE)
        UNIVERSE_FILE = _a.get("universe_file", UNIVERSE_FILE)
        GAP_MIN_PCT = _a.get("gap_min_pct", GAP_MIN_PCT)
        GAP_MIN_PCT_SMALLCAP = _a.get("gap_min_pct_smallcap", GAP_MIN_PCT_SMALLCAP)
        PRICE_MIN = _a.get("price_min", PRICE_MIN)
        AVG_VOL_MIN = _a.get("avg_vol_min", AVG_VOL_MIN)
        RVOL_MIN = _a.get("rvol_min", RVOL_MIN)
        MARKET_CAP_MIN_USD = _a.get("market_cap_min_usd", MARKET_CAP_MIN_USD)
        SMALLCAP_MAX_USD = _a.get("smallcap_max_usd", SMALLCAP_MAX_USD)
        TOP_N = _a.get("top_n", TOP_N)
        _b = _r.get("scanner_b", {})
        SCANNER_B_TICKERS = _b.get("tickers", SCANNER_B_TICKERS)
        SMA_LEN = _b.get("sma_len", SMA_LEN)
        SCANNER_B_RVOL_MIN = _b.get("rvol_min", SCANNER_B_RVOL_MIN)
        _c = _r.get("scanner_c_crypto", {})
        CRYPTO_SMA_LEN = _c.get("sma_len", CRYPTO_SMA_LEN)
        CRYPTO_DON_LEN = _c.get("don_len", CRYPTO_DON_LEN)
        CRYPTO_LOOKBACK_DAYS = _c.get("lookback_days", CRYPTO_LOOKBACK_DAYS)
        CRYPTO_FWD_DAYS = _c.get("fwd_days", CRYPTO_FWD_DAYS)
        RULES = _r
    except Exception as _e:  # malformed rules.json -> keep defaults
        print(f"[config] could not parse rules.json: {_e}")
        RULES = {}
else:
    RULES = {}

# Premarket-gap discovery universe for Scanner A.
# Liquid large/mega caps + common momentum/small-cap movers. Edit freely or
# drop a newline-separated list in universe.txt to override.
DEFAULT_UNIVERSE = [
    # mega/large cap tech
    "NVDA", "AMD", "MU", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA",
    "AVGO", "INTC", "QCOM", "ARM", "SMCI", "DELL", "PLTR", "CRM", "ORCL",
    "ADBE", "NFLX", "CSCO", "TXN", "AMAT", "LRCX", "ASML", "MRVL",
    # semis / ai adjacent
    "TSM", "ON", "WOLF", "NXPI", "STM", "AEHR", "INDI",
    # ev / clean
    "RIVN", "LCID", "NIO", "XPEV", "LI", "PLUG", "CHPT", "RUN", "FSLR", "ENPH",
    # biotech / pharma momentum
    "MRNA", "PFE", "BNTX", "VKTX", "CRSP", "IOVA", "RXRX",
    # consumer / meme / retail
    "GME", "AMC", "RDDT", "HOOD", "SOFI", "COIN", "MARA", "RIOT", "CLSK",
    "DKNG", "ABNB", "UBER", "LYFT", "SHOP", "ROKU", "PYPL", "XYZ", "AFRM",
    # indices/etf reference (filtered out by price/gap typically)
    "SPY", "QQQ", "IWM",
    # energy / materials / industrials movers
    "OXY", "CCJ", "UEC", "FCX", "CLF", "NUE", "AA", "BA", "GE", "CAT",
    # china / adr momentum
    "BABA", "PDD", "JD", "BIDU",
]
