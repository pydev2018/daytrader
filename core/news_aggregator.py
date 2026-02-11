"""
===============================================================================
  News Aggregator — Automated Financial News & Economic Data Integration
===============================================================================
  Aggregates real-time news from multiple institutional sources:
  • Economic calendars (NFP, CPI, interest rates, GDP, FOMC)
  • Central bank announcements (Fed, ECB, BOE, BOJ, RBA, SNB)
  • Breaking financial news (via APIs)
  • Market-specific data (CFTC COT positioning)
  • Sentiment analysis via AI
===============================================================================
"""

from __future__ import annotations

import os
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
from pathlib import Path

import requests
import feedparser

from dotenv import load_dotenv
from utils.logger import get_logger

log = get_logger("news_aggregator")

# Reload .env to get latest keys
load_dotenv(Path(__file__).parent.parent / ".env", override=True)

# ═════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

def _get_api_key(name: str) -> str:
    """Get API key, reloading from .env if needed."""
    return os.getenv(name, "")

# Cache to avoid duplicate processing
NEWS_CACHE_PATH = Path(__file__).parent.parent / "data" / "news_cache.json"
NEWS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

# Economic calendar cache (separate, updated less frequently)
ECON_CALENDAR_PATH = Path(__file__).parent.parent / "data" / "economic_calendar.json"

# Rate limiting
_last_api_call: Dict[str, float] = {}
_min_interval = {
    "alpha_vantage": 15,   # Free tier: 5 calls/min, 500/day
    "finnhub": 1,          # Free tier: 60 calls/min
    "newsapi": 10,         # Free tier: 100 calls/day
    "investing": 60,       # Scraping - be respectful
}


def _rate_limit(api_name: str):
    """Enforce rate limiting for API calls."""
    now = time.time()
    last_call = _last_api_call.get(api_name, 0)
    min_interval = _min_interval.get(api_name, 1)
    
    if now - last_call < min_interval:
        sleep_time = min_interval - (now - last_call)
        time.sleep(sleep_time)
    
    _last_api_call[api_name] = time.time()


# ── In-memory cache (loaded once from disk, written back periodically) ────
_mem_cache: dict | None = None
_cache_dirty: bool = False


def _load_cache() -> dict:
    """Load processed news cache into memory (only reads disk once)."""
    global _mem_cache
    if _mem_cache is not None:
        return _mem_cache
    if NEWS_CACHE_PATH.exists():
        try:
            with open(NEWS_CACHE_PATH, "r", encoding="utf-8") as f:
                _mem_cache = json.load(f)
                return _mem_cache
        except Exception:
            _mem_cache = {}
            return _mem_cache
    _mem_cache = {}
    return _mem_cache


def _save_cache(cache: dict):
    """Save processed news cache to disk (atomic write)."""
    global _cache_dirty
    try:
        import tempfile
        fd, tmp_path = tempfile.mkstemp(
            dir=NEWS_CACHE_PATH.parent, suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
        if NEWS_CACHE_PATH.exists():
            NEWS_CACHE_PATH.unlink()
        Path(tmp_path).rename(NEWS_CACHE_PATH)
        _cache_dirty = False
    except Exception as e:
        log.warning(f"Failed to save news cache: {e}")


def _is_cached(news_id: str) -> bool:
    """Check if news item was already processed (in-memory, no disk I/O)."""
    if not news_id:
        return True  # Skip empty IDs
    cache = _load_cache()
    return news_id in cache


def _add_to_cache(news_id: str, data: dict):
    """Add news item to in-memory cache. Flushes to disk periodically."""
    global _cache_dirty
    if not news_id:
        return
    cache = _load_cache()
    cache[news_id] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data
    }
    _cache_dirty = True
    # Keep only last 500 items
    if len(cache) > 500:
        sorted_items = sorted(cache.items(), key=lambda x: x[1].get("timestamp", ""))
        _mem_cache_update = dict(sorted_items[-500:])
        cache.clear()
        cache.update(_mem_cache_update)
    # Flush to disk every 20 new items
    if _cache_dirty and len(cache) % 20 == 0:
        _save_cache(cache)


# ═════════════════════════════════════════════════════════════════════════════
#  HIGH-IMPACT ECONOMIC EVENTS (HARDCODED SCHEDULE)
# ═════════════════════════════════════════════════════════════════════════════

# Major events that cause extreme volatility - avoid trading during these
HIGH_IMPACT_EVENTS = {
    # Format: (day_of_week, hour_utc, minute_utc, event_name, affected_currencies)
    # NFP - First Friday of month at 13:30 UTC (8:30 AM ET)
    "NFP": {"currencies": ["USD"], "avoid_minutes_before": 30, "avoid_minutes_after": 60},
    # FOMC - 8 times/year at 19:00 UTC (2:00 PM ET)
    "FOMC": {"currencies": ["USD"], "avoid_minutes_before": 60, "avoid_minutes_after": 120},
    # ECB Rate Decision - ~13:15 UTC
    "ECB": {"currencies": ["EUR"], "avoid_minutes_before": 30, "avoid_minutes_after": 90},
    # BOE Rate Decision - ~12:00 UTC
    "BOE": {"currencies": ["GBP"], "avoid_minutes_before": 30, "avoid_minutes_after": 60},
    # BOJ - varies
    "BOJ": {"currencies": ["JPY"], "avoid_minutes_before": 30, "avoid_minutes_after": 60},
}


def is_high_impact_event_window() -> Optional[Dict]:
    """
    Check if we're currently in a high-impact event window.
    Returns event info if in window, None otherwise.
    """
    # This would need a proper economic calendar API for real-time data
    # For now, we rely on the news feeds detecting upcoming events
    return None


# ═════════════════════════════════════════════════════════════════════════════
#  TIER 1: FINNHUB (Best Free API for Forex News + Economic Calendar)
# ═════════════════════════════════════════════════════════════════════════════

def fetch_finnhub_forex_news() -> List[Dict]:
    """
    Fetch real-time forex news from Finnhub.
    Free tier: 60 calls/minute
    """
    api_key = _get_api_key("FINNHUB_KEY")
    if not api_key:
        return []
    
    _rate_limit("finnhub")
    
    try:
        url = "https://finnhub.io/api/v1/news"
        params = {
            "category": "forex",
            "token": api_key,
        }
        
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            log.warning(f"Finnhub news API returned {resp.status_code}")
            return []
        
        data = resp.json()
        if not isinstance(data, list):
            return []
        
        events = []
        for item in data[:20]:
            news_id = str(item.get("id", ""))
            if _is_cached(news_id):
                continue
            
            timestamp = item.get("datetime", 0)
            if timestamp:
                time_str = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
            else:
                time_str = ""
            
            event = {
                "source": "Finnhub",
                "type": "news",
                "title": item.get("headline", ""),
                "summary": item.get("summary", "")[:500],
                "url": item.get("url", ""),
                "time": time_str,
                "category": item.get("category", ""),
                "related": item.get("related", ""),
            }
            events.append(event)
            _add_to_cache(news_id, event)
        
        if events:
            log.info(f"Finnhub: fetched {len(events)} forex news items")
        return events
    
    except Exception as e:
        log.warning(f"Finnhub news fetch failed: {e}")
        return []


def fetch_finnhub_economic_calendar() -> List[Dict]:
    """
    Fetch economic calendar from Finnhub.
    Shows upcoming high-impact events like NFP, CPI, FOMC.
    """
    api_key = _get_api_key("FINNHUB_KEY")
    if not api_key:
        return []
    
    _rate_limit("finnhub")
    
    try:
        # Get calendar for next 7 days
        from_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        to_date = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")
        
        url = "https://finnhub.io/api/v1/calendar/economic"
        params = {
            "from": from_date,
            "to": to_date,
            "token": api_key,
        }
        
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        
        data = resp.json()
        calendar_data = data.get("economicCalendar", [])
        
        events = []
        for item in calendar_data:
            # Filter for high/medium impact only
            impact = item.get("impact", "").lower()
            if impact not in ["high", "medium"]:
                continue
            
            event_id = f"{item.get('event', '')}_{item.get('time', '')}"
            if _is_cached(event_id):
                continue
            
            event = {
                "source": "Finnhub_Calendar",
                "type": "economic_event",
                "title": item.get("event", ""),
                "country": item.get("country", ""),
                "time": item.get("time", ""),
                "impact": impact,
                "actual": item.get("actual"),
                "estimate": item.get("estimate"),
                "prev": item.get("prev"),
                "unit": item.get("unit", ""),
            }
            events.append(event)
            _add_to_cache(event_id, event)
        
        if events:
            log.info(f"Finnhub Calendar: {len(events)} upcoming economic events")
        return events
    
    except Exception as e:
        log.warning(f"Finnhub calendar fetch failed: {e}")
        return []


# ═════════════════════════════════════════════════════════════════════════════
#  TIER 2: ALPHA VANTAGE (News Sentiment + Market Data)
# ═════════════════════════════════════════════════════════════════════════════

def fetch_alpha_vantage_news() -> List[Dict]:
    """
    Fetch market news with sentiment from Alpha Vantage.
    Free tier: 5 calls/min, 500 calls/day
    """
    api_key = _get_api_key("ALPHA_VANTAGE_KEY")
    if not api_key:
        return []
    
    _rate_limit("alpha_vantage")
    
    try:
        url = "https://www.alphavantage.co/query"
        params = {
            "function": "NEWS_SENTIMENT",
            "topics": "economy_fiscal,economy_monetary,economy_macro,financial_markets,forex",
            "sort": "LATEST",
            "limit": 20,
            "apikey": api_key,
        }
        
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        
        data = resp.json()
        
        # Check for API limit message
        if "Note" in data or "Information" in data:
            log.warning("Alpha Vantage API limit reached")
            return []
        
        events = []
        for item in data.get("feed", [])[:15]:
            news_id = item.get("url", "")
            if _is_cached(news_id):
                continue
            
            # Extract ticker sentiments
            ticker_sentiments = []
            for ts in item.get("ticker_sentiment", [])[:5]:
                ticker_sentiments.append({
                    "ticker": ts.get("ticker", ""),
                    "sentiment": ts.get("ticker_sentiment_label", ""),
                    "score": float(ts.get("ticker_sentiment_score", 0)),
                })
            
            event = {
                "source": "AlphaVantage",
                "type": "news",
                "title": item.get("title", ""),
                "summary": item.get("summary", "")[:500],
                "url": news_id,
                "time": item.get("time_published", ""),
                "sentiment": item.get("overall_sentiment_label", "neutral"),
                "sentiment_score": float(item.get("overall_sentiment_score", 0)),
                "ticker_sentiments": ticker_sentiments,
            }
            events.append(event)
            _add_to_cache(news_id, event)
        
        if events:
            log.info(f"AlphaVantage: fetched {len(events)} news items with sentiment")
        return events
    
    except Exception as e:
        log.warning(f"AlphaVantage fetch failed: {e}")
        return []


# ═════════════════════════════════════════════════════════════════════════════
#  TIER 3: NEWSAPI (Breaking Headlines)
# ═════════════════════════════════════════════════════════════════════════════

def fetch_newsapi_headlines() -> List[Dict]:
    """
    Fetch breaking financial headlines from NewsAPI.
    Free tier: 100 requests/day
    """
    api_key = _get_api_key("NEWSAPI_KEY")
    if not api_key:
        return []
    
    _rate_limit("newsapi")
    
    try:
        url = "https://newsapi.org/v2/top-headlines"
        params = {
            "category": "business",
            "language": "en",
            "pageSize": 20,
            "apiKey": api_key,
        }
        
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        
        data = resp.json()
        if data.get("status") != "ok":
            return []
        
        # Keywords that matter for forex trading
        forex_keywords = [
            "fed", "federal reserve", "ecb", "boe", "boj", "rba", "snb",
            "interest rate", "rate decision", "rate cut", "rate hike",
            "inflation", "cpi", "ppi", "gdp", "nfp", "payroll", "employment",
            "unemployment", "jobs", "labor", "labour",
            "dollar", "euro", "pound", "yen", "sterling", "franc",
            "forex", "currency", "exchange rate",
            "gold", "oil", "crude", "commodity",
            "tariff", "trade war", "sanctions",
            "powell", "lagarde", "bailey", "ueda",
            "treasury", "bond", "yield",
        ]
        
        events = []
        for item in data.get("articles", []):
            news_id = item.get("url", "")
            if _is_cached(news_id):
                continue
            
            title = (item.get("title") or "").lower()
            description = (item.get("description") or "").lower()
            content = title + " " + description
            
            # Only include forex-relevant news
            if not any(kw in content for kw in forex_keywords):
                continue
            
            event = {
                "source": "NewsAPI",
                "type": "headline",
                "title": item.get("title", ""),
                "summary": item.get("description", "")[:500] if item.get("description") else "",
                "url": news_id,
                "time": item.get("publishedAt", ""),
                "publisher": item.get("source", {}).get("name", ""),
            }
            events.append(event)
            _add_to_cache(news_id, event)
        
        if events:
            log.info(f"NewsAPI: fetched {len(events)} relevant headlines")
        return events
    
    except Exception as e:
        log.warning(f"NewsAPI fetch failed: {e}")
        return []


# ═════════════════════════════════════════════════════════════════════════════
#  TIER 4: CENTRAL BANK RSS FEEDS (Free, No API Key)
# ═════════════════════════════════════════════════════════════════════════════

CENTRAL_BANK_FEEDS = {
    # Federal Reserve
    "Federal Reserve": [
        "https://www.federalreserve.gov/feeds/press_all.xml",
        "https://www.federalreserve.gov/feeds/press_monetary.xml",
    ],
    # European Central Bank
    "ECB": [
        "https://www.ecb.europa.eu/rss/press.html",
        "https://www.ecb.europa.eu/rss/fxref-usd.html",
    ],
    # Bank of England
    "Bank of England": [
        "https://www.bankofengland.co.uk/rss/news",
        "https://www.bankofengland.co.uk/rss/publications",
    ],
    # Bank of Japan
    "Bank of Japan": [
        "https://www.boj.or.jp/en/rss/whatsnew.xml",
    ],
    # Reserve Bank of Australia
    "RBA": [
        "https://www.rba.gov.au/rss/rss-cb-media-releases.xml",
    ],
    # Swiss National Bank
    "SNB": [
        "https://www.snb.ch/en/rss/mmr",
    ],
    # Bank of Canada
    "Bank of Canada": [
        "https://www.bankofcanada.ca/content-type/press-releases/feed/",
    ],
}


def fetch_central_bank_feeds() -> List[Dict]:
    """Fetch RSS feeds from major central banks. No API key required."""
    events = []
    
    for bank_name, feed_urls in CENTRAL_BANK_FEEDS.items():
        for feed_url in feed_urls:
            try:
                feed = feedparser.parse(feed_url)
                
                if feed.bozo and not feed.entries:
                    continue  # Feed parsing failed
                
                for entry in feed.entries[:3]:  # Top 3 from each feed
                    news_id = entry.get("link", "") or entry.get("id", "")
                    if _is_cached(news_id):
                        continue
                    
                    # Parse time
                    published = entry.get("published", "") or entry.get("updated", "")
                    
                    event = {
                        "source": bank_name,
                        "type": "central_bank",
                        "title": entry.get("title", ""),
                        "summary": entry.get("summary", "")[:500] if entry.get("summary") else "",
                        "url": news_id,
                        "time": published,
                        "category": "monetary_policy",
                    }
                    events.append(event)
                    _add_to_cache(news_id, event)
            
            except Exception as e:
                log.debug(f"{bank_name} RSS {feed_url} failed: {e}")
    
    if events:
        log.info(f"Central Banks: fetched {len(events)} announcements")
    return events


# ═════════════════════════════════════════════════════════════════════════════
#  TIER 5: CFTC COT REPORT (Institutional Positioning)
# ═════════════════════════════════════════════════════════════════════════════

def fetch_cftc_cot_data() -> Optional[Dict]:
    """
    Fetch CFTC Commitment of Traders data.
    Shows how institutions are positioned (long/short) in futures.
    Published every Friday at 3:30 PM ET for positions as of Tuesday.
    """
    try:
        # CFTC provides data in various formats
        # We'll use the Quandl-style API endpoint (now on data.nasdaq.com)
        # Note: This requires a free Nasdaq Data Link API key for full access
        
        # For now, we'll just note that COT data exists
        # Full implementation would parse:
        # - Commercial positions (hedgers)
        # - Non-commercial positions (speculators/institutions)
        # - Open interest changes
        
        now = datetime.now(timezone.utc)
        
        # COT is released Friday 3:30 PM ET (7:30 PM UTC in winter, 6:30 PM UTC in summer)
        if now.weekday() == 4 and 18 <= now.hour <= 23:  # Friday evening UTC
            return {
                "source": "CFTC",
                "type": "cot_report",
                "title": "New COT Report Released",
                "summary": "Weekly Commitment of Traders report available. Check institutional positioning.",
                "time": now.isoformat(),
            }
        
        return None
    
    except Exception as e:
        log.warning(f"CFTC COT check failed: {e}")
        return None


# ═════════════════════════════════════════════════════════════════════════════
#  AGGREGATOR & AI ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════

def aggregate_all_news() -> List[Dict]:
    """Fetch news from all sources and aggregate."""
    all_news = []
    
    # Tier 1: Finnhub (most reliable for forex)
    all_news.extend(fetch_finnhub_forex_news())
    all_news.extend(fetch_finnhub_economic_calendar())
    
    # Tier 2: Alpha Vantage (sentiment)
    all_news.extend(fetch_alpha_vantage_news())
    
    # Tier 3: NewsAPI (headlines)
    all_news.extend(fetch_newsapi_headlines())
    
    # Tier 4: Central banks (no API key needed)
    all_news.extend(fetch_central_bank_feeds())
    
    # Tier 5: CFTC COT (weekly)
    cot = fetch_cftc_cot_data()
    if cot:
        all_news.append(cot)
    
    # Sort by time (most recent first)
    def get_time(item):
        t = item.get("time", "")
        if not t:
            return ""
        return t
    
    all_news.sort(key=get_time, reverse=True)
    
    log.info(f"News Aggregator: {len(all_news)} total items from all sources")
    return all_news


def analyze_news_impact(news_items: List[Dict]) -> Optional[Dict]:
    """
    Use AI to analyze aggregated news for trading impact.
    Returns affected instruments and avoid list.
    """
    if not news_items:
        return None
    
    # Lazy import to avoid circular dependency
    from core import ai_analyst
    
    # Build structured news summary for AI
    news_lines = []
    for item in news_items[:25]:  # Top 25 items
        source = item.get("source", "Unknown")
        title = item.get("title", "")
        summary = item.get("summary", "")[:150]
        item_type = item.get("type", "news")
        
        if item_type == "economic_event":
            impact = item.get("impact", "")
            country = item.get("country", "")
            news_lines.append(f"[{source}] ECONOMIC EVENT ({impact} impact, {country}): {title}")
        elif item_type == "central_bank":
            news_lines.append(f"[{source}] CENTRAL BANK: {title}")
        else:
            news_lines.append(f"[{source}] {title}")
            if summary:
                news_lines.append(f"    {summary}")
    
    news_summary = "\n".join(news_lines)
    
    # Send to AI analyst
    try:
        impact = ai_analyst.analyze_news(news_summary)
        
        if impact:
            affected = len(impact.get("affected_instruments", []))
            avoid = len(impact.get("avoid_trading", []))
            log.info(f"AI News Analysis: {affected} instruments affected, {avoid} to avoid")
        
        return impact
    
    except Exception as e:
        log.warning(f"AI news analysis failed: {e}")
        return None


def get_latest_news_impact() -> Optional[Dict]:
    """
    Main function: Fetch all news and return trading impact analysis.
    Call this from the scanner before generating signals.
    """
    try:
        # Fetch all news
        news_items = aggregate_all_news()
        
        if not news_items:
            log.debug("No new news items found")
            return None
        
        # Analyze impact
        impact = analyze_news_impact(news_items)
        
        return impact
    
    except Exception as e:
        log.error(f"News aggregation failed: {e}", exc_info=True)
        return None


# ═════════════════════════════════════════════════════════════════════════════
#  SYMBOL FILTERING
# ═════════════════════════════════════════════════════════════════════════════

def should_avoid_symbol(symbol: str, news_impact: Optional[Dict]) -> bool:
    """
    Check if a symbol should be avoided based on news impact.
    """
    if not news_impact:
        return False
    
    avoid_list = news_impact.get("avoid_trading", [])
    if not avoid_list:
        return False
    
    symbol_upper = symbol.upper()
    
    for avoid_sym in avoid_list:
        avoid_upper = avoid_sym.upper()
        # Check for exact match or currency component match
        if avoid_upper in symbol_upper:
            log.info(f"{symbol}: AVOIDING due to news impact on {avoid_sym}")
            return True
        # Check individual currencies (e.g., "USD" matches "EURUSD")
        if len(avoid_upper) == 3:  # Currency code
            if avoid_upper in symbol_upper:
                log.info(f"{symbol}: AVOIDING due to news impact on {avoid_upper}")
                return True
    
    return False


def get_news_confidence_adjustment(symbol: str, direction: str, news_impact: Optional[Dict]) -> int:
    """
    Adjust confidence score based on news impact.
    
    Returns
    -------
    int
        Confidence adjustment (-15 to +10)
    """
    if not news_impact:
        return 0
    
    affected = news_impact.get("affected_instruments", [])
    if not affected:
        return 0
    
    symbol_upper = symbol.upper()
    
    for item in affected:
        item_symbol = item.get("symbol", "").upper()
        
        # Check if this news affects our symbol
        matches = False
        if item_symbol in symbol_upper:
            matches = True
        elif len(item_symbol) == 3 and item_symbol in symbol_upper:  # Currency code
            matches = True
        
        if not matches:
            continue
        
        impact = item.get("impact", "neutral").lower()
        severity = item.get("severity", "low").lower()
        
        # Determine adjustment based on alignment
        if direction == "BUY":
            if impact == "bullish":
                return 10 if severity == "high" else 5
            elif impact == "bearish":
                return -15 if severity == "high" else -8
        
        elif direction == "SELL":
            if impact == "bearish":
                return 10 if severity == "high" else 5
            elif impact == "bullish":
                return -15 if severity == "high" else -8
    
    return 0


# ═════════════════════════════════════════════════════════════════════════════
#  UPCOMING EVENTS CHECK
# ═════════════════════════════════════════════════════════════════════════════

def get_upcoming_high_impact_events(hours_ahead: int = 4) -> List[Dict]:
    """
    Get high-impact economic events in the next N hours.
    Useful for avoiding trades before major announcements.
    """
    api_key = _get_api_key("FINNHUB_KEY")
    if not api_key:
        return []
    
    try:
        now = datetime.now(timezone.utc)
        from_date = now.strftime("%Y-%m-%d")
        to_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        
        url = "https://finnhub.io/api/v1/calendar/economic"
        params = {
            "from": from_date,
            "to": to_date,
            "token": api_key,
        }
        
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return []
        
        data = resp.json()
        calendar = data.get("economicCalendar", [])
        
        upcoming = []
        for item in calendar:
            if item.get("impact", "").lower() != "high":
                continue
            
            event_time_str = item.get("time", "")
            if not event_time_str:
                continue
            
            try:
                # Parse event time
                event_time = datetime.fromisoformat(event_time_str.replace("Z", "+00:00"))
                hours_until = (event_time - now).total_seconds() / 3600
                
                if 0 <= hours_until <= hours_ahead:
                    upcoming.append({
                        "event": item.get("event", ""),
                        "country": item.get("country", ""),
                        "time": event_time_str,
                        "hours_until": round(hours_until, 1),
                        "impact": "high",
                    })
            except Exception:
                continue
        
        return upcoming
    
    except Exception as e:
        log.warning(f"Failed to check upcoming events: {e}")
        return []
