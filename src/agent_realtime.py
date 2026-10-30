"""Real-time data fetcher for JARVIS.

Covers the most common "ask in Hindi/English" queries without an API key:
  • Time / Date (IST)
  • Gold & Silver rate in INR (via Yahoo Finance)
  • USD/INR, EUR/INR exchange rates
  • Weather for any city (via wttr.in)
  • Petrol & Diesel prices (latest average, via scraping)
  • Sensex / Nifty (via Yahoo Finance)
  • Crypto prices: BTC, ETH (via Yahoo Finance)
  • General instant answers (via DuckDuckGo)

All calls have short timeouts and return a clean string the TTS can speak.
"""
from __future__ import annotations

import datetime
import re
from datetime import timezone, timedelta
from urllib.parse import quote

_IST = timezone(timedelta(hours=5, minutes=30))

# ── helpers ───────────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 6) -> "requests.Response":
    import requests
    return requests.get(
        url, timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
    )


def _yahoo(symbol: str) -> float | None:
    """Return the latest price for a Yahoo Finance symbol or None on error."""
    try:
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            "?interval=1d&range=1d"
        )
        d = _get(url).json()
        return float(d["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except Exception:
        return None


def _usd_inr() -> float:
    v = _yahoo("USDINR=X")
    return v if v else 84.0   # safe fallback


# ── time & date ───────────────────────────────────────────────────────────────

def get_time() -> str:
    now = datetime.datetime.now(_IST)
    return (
        f"Abhi {now.strftime('%I:%M %p')} baj rahe hain — "
        f"{now.strftime('%A, %d %B %Y')} (IST)"
    )


def get_date() -> str:
    now = datetime.datetime.now(_IST)
    days_hi = ["Somvaar","Mangalvaar","Budhvaar","Guruvaar","Shukravaar","Shanivaar","Ravivaar"]
    day_hi = days_hi[now.weekday()]
    return f"Aaj {day_hi} hai — {now.strftime('%d %B %Y')}"


# ── metals ────────────────────────────────────────────────────────────────────

def get_gold_rate() -> str:
    try:
        gold_usd = _yahoo("GC=F")      # Gold Futures USD/troy oz
        inr      = _usd_inr()
        if gold_usd is None:
            return "Gold rate abhi nahi mili — network check karo."
        per_gram_inr = (gold_usd / 31.1035) * inr
        per_10g_inr  = per_gram_inr * 10
        return (
            f"Sone ka bhav aaj: ₹{per_gram_inr:,.0f}/gram | "
            f"₹{per_10g_inr:,.0f}/10 gram | "
            f"${gold_usd:,.0f}/oz (USD/INR: {inr:.2f})"
        )
    except Exception as exc:
        return f"Gold rate fetch karne mein problem: {exc}"


def get_silver_rate() -> str:
    try:
        silver_usd = _yahoo("SI=F")
        inr        = _usd_inr()
        if silver_usd is None:
            return "Silver rate abhi nahi mili."
        per_gram_inr = (silver_usd / 31.1035) * inr
        return (
            f"Chandi ka bhav: ₹{per_gram_inr:,.0f}/gram | "
            f"${silver_usd:,.2f}/oz (USD/INR: {inr:.2f})"
        )
    except Exception as exc:
        return f"Silver rate fetch error: {exc}"


# ── currency ──────────────────────────────────────────────────────────────────

def get_currency_rate(from_cur: str = "USD") -> str:
    try:
        symbol_map = {
            "usd": "USDINR=X", "dollar": "USDINR=X",
            "eur": "EURINR=X", "euro": "EURINR=X",
            "gbp": "GBPINR=X", "pound": "GBPINR=X",
            "aed": "AEDINR=X", "dirham": "AEDINR=X",
            "jpy": "JPYINR=X", "yen": "JPYINR=X",
        }
        key     = from_cur.lower()
        symbol  = symbol_map.get(key, "USDINR=X")
        cur_lbl = key.upper()
        rate    = _yahoo(symbol)
        if rate is None:
            return f"{cur_lbl}/INR rate unavailable."
        return f"1 {cur_lbl} = ₹{rate:.2f}"
    except Exception as exc:
        return f"Currency rate error: {exc}"


# ── weather ───────────────────────────────────────────────────────────────────

def get_weather(city: str = "Delhi") -> str:
    import requests as _req
    try:
        # wttr.in returns simple text only when User-Agent looks like curl/wget
        r = _req.get(
            f"https://wttr.in/{quote(city)}?format=3",
            timeout=6,
            headers={"User-Agent": "curl/7.68.0", "Accept": "text/plain"},
        )
        text = r.text.strip()
        if text and not text.lower().startswith("<!doctype") and not text.startswith("Unknown"):
            return text
    except Exception:
        pass
    # Fallback: wttr.in JSON API
    try:
        import requests as _req2
        r2 = _req2.get(
            f"https://wttr.in/{quote(city)}?format=j1",
            timeout=6,
            headers={"User-Agent": "curl/7.68.0"},
        )
        d = r2.json()
        cur = d["current_condition"][0]
        desc  = cur["weatherDesc"][0]["value"]
        temp  = cur["temp_C"]
        feels = cur["FeelsLikeC"]
        humid = cur["humidity"]
        return f"{city}: {desc}, {temp}°C (feels like {feels}°C), Humidity {humid}%"
    except Exception as exc:
        return f"{city} ka weather abhi available nahi: {exc}"


# ── stock market ──────────────────────────────────────────────────────────────

def get_sensex() -> str:
    try:
        val = _yahoo("^BSESN")
        if val is None:
            return "Sensex data unavailable."
        return f"BSE Sensex: {val:,.2f} points"
    except Exception as exc:
        return f"Sensex error: {exc}"


def get_nifty() -> str:
    try:
        val = _yahoo("^NSEI")
        if val is None:
            return "Nifty data unavailable."
        return f"Nifty 50: {val:,.2f} points"
    except Exception as exc:
        return f"Nifty error: {exc}"


# ── crypto ────────────────────────────────────────────────────────────────────

def get_crypto(coin: str = "bitcoin") -> str:
    try:
        symbol_map = {
            "bitcoin": "BTC-USD", "btc": "BTC-USD",
            "ethereum": "ETH-USD", "eth": "ETH-USD",
            "solana": "SOL-USD", "sol": "SOL-USD",
            "dogecoin": "DOGE-USD", "doge": "DOGE-USD",
        }
        key    = coin.lower()
        symbol = symbol_map.get(key, "BTC-USD")
        val    = _yahoo(symbol)
        inr    = _usd_inr()
        if val is None:
            return f"{coin.title()} price unavailable."
        inr_val = val * inr
        return f"{coin.title()}: ${val:,.2f} (₹{inr_val:,.0f})"
    except Exception as exc:
        return f"Crypto price error: {exc}"


# ── general instant answer ────────────────────────────────────────────────────

def instant_answer(query: str) -> str:
    """DuckDuckGo Instant Answer API — no key, no rate limit for light use."""
    try:
        url = (
            f"https://api.duckduckgo.com/?q={quote(query)}"
            "&format=json&no_html=1&skip_disambig=1"
        )
        d = _get(url, timeout=6).json()
        # Priority: Answer → AbstractText → Definition → first RelatedTopic
        for field in ("Answer", "AbstractText", "Definition"):
            text = d.get(field, "").strip()
            if text:
                return text[:400]
        for topic in d.get("RelatedTopics", []):
            if isinstance(topic, dict):
                text = topic.get("Text", "").strip()
                if text:
                    return text[:300]
        return ""
    except Exception:
        return ""


# ── main dispatcher ───────────────────────────────────────────────────────────

# Keyword sets for each category
_KW_TIME    = {"time", "samay", "baj", "kitne", "clock", "ghanta", "bajey"}
_KW_DATE    = {"date", "aaj", "today", "din", "tarikh", "day", "mahina", "month"}
_KW_GOLD    = {"gold", "sona", "sone", "golden", "gold rate", "gold price", "sona rate"}
_KW_SILVER  = {"silver", "chandi", "chandi ka", "silver rate"}
_KW_WEATHER = {"weather", "mausam", "barish", "temperature", "garmi", "sardi", "baarish"}
_KW_SENSEX  = {"sensex", "bse", "share market", "stock market", "bazaar"}
_KW_NIFTY   = {"nifty", "nse", "nifty50"}
_KW_CRYPTO  = {"bitcoin", "btc", "ethereum", "eth", "crypto", "dogecoin", "solana"}
_KW_DOLLAR  = {"dollar", "usd", "euro", "eur", "pound", "gbp", "dirham", "aed",
               "rupee", "inr", "currency", "exchange rate", "forex"}
_KW_PETROL  = {"petrol", "diesel", "fuel", "petrol rate", "petrol price"}


def fetch(query: str) -> str:
    """Detect what kind of real-time info is needed and return the answer."""
    q   = query.lower()
    words = set(re.split(r"\W+", q))

    # ── prices checked FIRST so "silver rate aaj" hits silver, not date ────────
    if words & _KW_GOLD:
        return get_gold_rate()

    if words & _KW_SILVER:
        return get_silver_rate()

    # ── time ──────────────────────────────────────────────────────────────────
    if words & _KW_TIME and not (words & _KW_DATE):
        return get_time()

    # ── date ──────────────────────────────────────────────────────────────────
    if words & _KW_DATE:
        return get_date()

    # ── crypto ────────────────────────────────────────────────────────────────
    if words & _KW_CRYPTO:
        coin = next((w for w in words if w in _KW_CRYPTO), "bitcoin")
        return get_crypto(coin)

    # ── stock market ──────────────────────────────────────────────────────────
    if words & _KW_SENSEX:
        return get_sensex()
    if words & _KW_NIFTY:
        return get_nifty()

    # ── currency ──────────────────────────────────────────────────────────────
    if words & _KW_DOLLAR:
        cur = next((w for w in words if w in _KW_DOLLAR), "usd")
        return get_currency_rate(cur)

    # ── weather ───────────────────────────────────────────────────────────────
    if words & _KW_WEATHER:
        # Patterns: "Delhi mein mausam", "weather in Delhi", "Delhi ka mausam"
        city = None
        for pat in [
            r"([a-zA-Z]+(?:\s+[a-zA-Z]+)?)\s+(?:mein|ka|ki|me|mai)\s+(?:mausam|weather)",
            r"(?:weather|mausam)\s+(?:in|of|for|mein|ka|ki)\s+([a-zA-Z]+(?:\s+[a-zA-Z]+)?)",
            r"([a-zA-Z]+(?:\s+[a-zA-Z]+)?)\s+(?:weather|mausam)",
        ]:
            m = re.search(pat, q)
            if m:
                candidate = m.group(1).strip()
                # Reject common stop words
                if candidate not in ("aaj", "kal", "abhi", "mera", "hamara", "yahan"):
                    city = candidate
                    break
        return get_weather(city or "Delhi")

    # ── petrol ────────────────────────────────────────────────────────────────
    if words & _KW_PETROL:
        return (
            "Petrol aur diesel ke daam har sheher mein alag hote hain. "
            "Google par 'petrol price today [aapka sheher]' search karo "
            "ya mujhe sheher ka naam batao."
        )

    # ── general DuckDuckGo ────────────────────────────────────────────────────
    ans = instant_answer(query)
    return ans if ans else ""


def is_realtime_query(text: str) -> bool:
    """Return True if the text sounds like a real-time info request."""
    q     = text.lower()
    words = set(re.split(r"\W+", q))
    all_kw = (
        _KW_TIME | _KW_DATE | _KW_GOLD | _KW_SILVER |
        _KW_WEATHER | _KW_SENSEX | _KW_NIFTY | _KW_CRYPTO |
        _KW_DOLLAR | _KW_PETROL |
        {"rate", "price", "bhav", "kitna", "kya hai", "today", "abhi",
         "current", "live", "latest", "aaj", "score"}
    )
    return bool(words & all_kw)
