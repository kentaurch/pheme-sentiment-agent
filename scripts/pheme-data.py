#!/usr/bin/env python3
"""
pheme-data.py — Sentiment Data Fetcher for Pheme Sentiment Analysis

Fetches Fear & Greed Index, funding rates, OI trends, social sentiment signals,
and market emotion metrics from public APIs.

Usage:
    python3 pheme-data.py --coin bitcoin
    python3 pheme-data.py --coin eth --json
    python3 pheme-data.py --list-coins

Dependencies: urllib (stdlib). Optional: requests.
"""

import argparse
import json
import os
import sys
import time
import functools
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Retry Decorator ─────────────────────────────────────────────────────────────

def retry(max_attempts=3, delay=2):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise
                    print(f'  [retry] Attempt {attempt+1} failed: {e}. Retrying in {delay}s...', file=sys.stderr)
                    time.sleep(delay)
            return None
        return wrapper
    return decorator

# ── Cache Decorator (file-based with TTL) ──────────────────────────────────────

CACHE_DIR = os.path.expanduser('~/.cache/telos-agents')
os.makedirs(CACHE_DIR, exist_ok=True)

def cached(ttl_seconds=300):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = f'{func.__name__}_{hash(str(args) + str(sorted(kwargs.items())))}'
            cache_path = os.path.join(CACHE_DIR, f'{cache_key}.json')
            if os.path.exists(cache_path):
                age = time.time() - os.path.getmtime(cache_path)
                if age < ttl_seconds:
                    with open(cache_path) as f:
                        return json.load(f)
            result = func(*args, **kwargs)
            if result is not None:
                with open(cache_path, 'w') as f:
                    json.dump(result, f)
            return result
        return wrapper
    return decorator

# ── Configuration ───────────────────────────────────────────────────────────────

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
ALTERNATIVE_ME_BASE = "https://api.alternative.me/fng"
COINGLASS_BASE = "https://api.coinglass.com/api/public/v2"  # public endpoints
SANTIMENT_BASE = "https://api.santiment.net/graphql"  # public API (rate limited)

COIN_IDS = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "cardano": "cardano", "ada": "cardano",
    "ripple": "ripple", "xrp": "ripple",
    "polkadot": "polkadot", "dot": "polkadot",
    "avalanche": "avalanche-2", "avax": "avalanche-2",
    "chainlink": "chainlink", "link": "chainlink",
    "polygon": "matic-network", "matic": "matic-network",
    "arbitrum": "arbitrum", "arb": "arbitrum",
    "optimism": "optimism", "op": "optimism",
    "sui": "sui",
    "aptos": "aptos", "apt": "aptos",
    "near": "near",
    "injective": "injective-protocol", "inj": "injective-protocol",
    "render": "render-token", "rndr": "render-token",
}

# ── Helpers ─────────────────────────────────────────────────────────────────────

def _fetch(url, timeout=15):
    """Fetch JSON from a URL. Returns dict or None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Pheme/3.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"_error": str(e)}


def _fetch_with_retry(url, timeout=15):
    """Fetch with retry logic for transient failures."""
    for attempt in range(3):
        try:
            result = _fetch(url, timeout)
            if result and "_error" not in result:
                return result
            if attempt < 2:
                print(f"  [retry] API call failed, retrying in 2s...", file=sys.stderr)
                time.sleep(2)
        except Exception as e:
            if attempt == 2:
                return {"_error": str(e)}
            time.sleep(2)
    return {"_error": "Max retries exceeded"}


def _fmt(val, suffix=""):
    """Format a number with commas, optional suffix."""
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if v >= 1_000_000_000:
            return f"${v / 1_000_000_000:,.2f}B{suffix}"
        if v >= 1_000_000:
            return f"${v / 1_000_000:,.2f}M{suffix}"
        if v >= 1_000:
            return f"${v:,.0f}{suffix}"
        return f"{v:,.4f}"
    except (ValueError, TypeError):
        return str(val)


def _pct(val):
    """Format a percentage."""
    if val is None:
        return "N/A"
    try:
        v = float(val)
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.2f}%"
    except (ValueError, TypeError):
        return str(val)


def _staleness_warning(timestamp_str, max_age_minutes=30):
    """Print a warning if data is older than max_age_minutes."""
    if not timestamp_str:
        return
    try:
        if isinstance(timestamp_str, (int, float)):
            ts = datetime.fromtimestamp(timestamp_str, tz=timezone.utc)
        else:
            ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds() / 60
        if age > max_age_minutes:
            print(f"  [stale] Data is {age:.0f} minutes old (threshold: {max_age_minutes}m)", file=sys.stderr)
    except Exception:
        pass


# ── Data Fetchers ───────────────────────────────────────────────────────────────

@retry(max_attempts=2, delay=2)
@cached(ttl_seconds=180)
def fear_greed_index():
    """Fetch Fear & Greed Index from alternative.me."""
    data = _fetch(f"{ALTERNATIVE_ME_BASE}/?limit=14")
    if not data or "data" not in data:
        return None

    values = data["data"]
    today = values[0]
    return {
        "value": int(today.get("value", 50)),
        "classification": today.get("value_classification", "Neutral"),
        "timestamp": today.get("timestamp"),
        "trend_7d": {
            "start_value": int(values[-1].get("value", 50)) if len(values) > 1 else None,
            "start_classification": values[-1].get("value_classification") if len(values) > 1 else None,
            "direction": (
                "rising" if int(values[0].get("value", 50)) > int(values[-1].get("value", 50))
                else "falling"
            ),
        },
        "values_14d": [int(v.get("value", 50)) for v in values],
    }


@retry(max_attempts=2, delay=2)
@cached(ttl_seconds=180)
def global_market_data():
    """Fetch global market sentiment indicators from CoinGecko."""
    data = _fetch(f"{COINGECKO_BASE}/global")
    if not data or "data" not in data:
        return None
    d = data["data"]
    return {
        "total_market_cap_usd": d.get("total_market_cap", {}).get("usd"),
        "total_volume_24h_usd": d.get("total_volume", {}).get("usd"),
        "btc_dominance": d.get("market_cap_percentage", {}).get("btc"),
        "eth_dominance": d.get("market_cap_percentage", {}).get("eth"),
        "active_cryptos": d.get("active_cryptocurrencies"),
        "market_cap_change_24h": d.get("market_cap_change_percentage_24h_usd"),
    }


@retry(max_attempts=2, delay=2)
@cached(ttl_seconds=180)
def coin_social_data(coin_id):
    """Fetch social & community data from CoinGecko."""
    url = (
        f"{COINGECKO_BASE}/coins/{coin_id}"
        f"?localization=false&tickers=false&community_data=true&developer_data=true"
    )
    data = _fetch(url)
    if not data or "market_data" not in data:
        return None

    cd = data.get("community_data", {})
    md = data.get("market_data", {})

    result = {
        "name": data.get("name"),
        "symbol": data.get("symbol", "").upper(),
        "twitter_followers": cd.get("twitter_followers"),
        "reddit_subscribers": cd.get("reddit_subscribers"),
        "telegram_users": cd.get("telegram_channel_user_count"),
        "price": md.get("current_price", {}).get("usd"),
        "price_change_24h": md.get("price_change_percentage_24h"),
        "price_change_7d": md.get("price_change_percentage_7d"),
        "market_cap": md.get("market_cap", {}).get("usd"),
        "volume_24h": md.get("total_volume", {}).get("usd"),
        "ath": md.get("ath", {}).get("usd"),
        "ath_date": md.get("ath_date", {}).get("usd"),
    }
    return result


@cached(ttl_seconds=600)
def funding_rate_data(coin_id):
    """Fetch estimated funding rate data (using CoinGecko derivatives as proxy)."""
    # CoinGecko doesn't have direct funding rate in free API.
    # We return implied sentiment from market data as fallback.
    data = _fetch(f"{COINGECKO_BASE}/coins/{coin_id}/tickers?include_exchange_logo=false")
    if not data or "tickers" not in data:
        return None

    tickers = data.get("tickers", [])
    perp_tickers = [t for t in tickers if t.get("trade_url") and "perp" in str(t.get("market", {})).lower()]

    rates = []
    for t in perp_tickers[:5]:
        rates.append({
            "exchange": t.get("market", {}).get("name", t.get("exchange", "unknown")),
            "pair": t.get("base", "") + "/" + t.get("target", ""),
            "converted_volume": t.get("converted_volume", {}).get("usd"),
            "last_price": t.get("last"),
        })

    return {
        "coin": coin_id,
        "perp_tickers_found": len(perp_tickers),
        "top_perp_markets": rates,
        "note": "Direct funding rates require CoinGlass or exchange API. Using perp volume as sentiment proxy."
    }


@cached(ttl_seconds=300)
def trending_searches():
    """Fetch trending coins — indicates social volume direction."""
    data = _fetch(f"{COINGECKO_BASE}/search/trending")
    if not data or "coins" not in data:
        return None
    trending = []
    for item in data["coins"][:20]:
        c = item.get("item", {})
        trending.append({
            "name": c.get("name"),
            "symbol": c.get("symbol"),
            "market_cap_rank": c.get("market_cap_rank"),
            "score": c.get("score"),
        })
    return trending


# ── Stale data detection ────────────────────────────────────────────────────────

def check_data_freshness(report, max_age_minutes=30):
    """Check all timestamps in report and warn about stale data."""
    stale_sections = []
    now = datetime.now(timezone.utc)

    for key, value in report.items():
        if isinstance(value, dict):
            ts = value.get("timestamp") or value.get("ath_date")
            if ts:
                try:
                    if isinstance(ts, (int, float)):
                        age = (now - datetime.fromtimestamp(ts, tz=timezone.utc)).total_seconds() / 60
                    else:
                        age = (now - datetime.fromisoformat(str(ts).replace("Z", "+00:00"))).total_seconds() / 60
                    if age > max_age_minutes:
                        stale_sections.append(key)
                except Exception:
                    pass

    if stale_sections:
        print(f"  [stale] Data freshness warning: {', '.join(stale_sections)} data is >{max_age_minutes}m old", file=sys.stderr)
    return stale_sections


# ── Report Builder ──────────────────────────────────────────────────────────────

def build_report(coin, json_mode=False):
    """Build a comprehensive sentiment analysis report."""
    coin_id = COIN_IDS.get(coin.lower(), coin.lower())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    report = {
        "timestamp": timestamp,
        "agent": "pheme-sentiment",
        "coin": coin,
        "coin_id": coin_id,
    }

    # 1. Fear & Greed
    try:
        report["fear_greed"] = fear_greed_index()
    except Exception as e:
        report["fear_greed"] = {"_error": str(e), "value": 50, "classification": "Unknown"}

    # 2. Global market
    try:
        report["global_market"] = global_market_data()
    except Exception as e:
        report["global_market"] = {"_error": str(e)}

    # 3. Social data
    try:
        report["social_data"] = coin_social_data(coin_id)
    except Exception as e:
        report["social_data"] = {"_error": str(e)}

    # 4. Funding rate proxy
    try:
        report["funding"] = funding_rate_data(coin_id)
    except Exception as e:
        report["funding"] = {"_error": str(e)}

    # 5. Trending
    try:
        report["trending"] = trending_searches()
    except Exception as e:
        report["trending"] = {"_error": str(e)}

    # 6. Staleness check
    check_data_freshness(report)

    if json_mode:
        return json.dumps(report, indent=2)

    return _format_human_report(report)


def _format_human_report(r):
    """Format the report for human reading."""
    lines = []
    lines.append(f"Pheme Sentiment Data Report — {r['coin'].upper()}")
    lines.append(f"Generated: {r['timestamp']}")
    lines.append("")

    # Fear & Greed
    fg = r.get("fear_greed", {})
    lines.append("── Market Emotion ──")
    if fg and "_error" not in fg:
        lines.append(f"  Fear & Greed       : {fg.get('value')} — {fg.get('classification')}")
        trend = fg.get("trend_7d", {})
        if trend.get("direction"):
            lines.append(f"  7-day Direction    : {trend['direction']}")
        vals = fg.get("values_14d", [])
        if vals:
            avg = sum(vals) / len(vals)
            lines.append(f"  14-day Avg         : {avg:.0f}")
    else:
        lines.append("  (unavailable)")
    lines.append("")

    # Global market
    gl = r.get("global_market", {})
    lines.append("── Global Market ──")
    if gl and "_error" not in gl:
        lines.append(f"  Total Market Cap   : {_fmt(gl.get('total_market_cap_usd'))}")
        lines.append(f"  24h Volume         : {_fmt(gl.get('total_volume_24h_usd'))}")
        lines.append(f"  BTC Dominance      : {gl.get('btc_dominance', 'N/A')}")
        lines.append(f"  ETH Dominance      : {gl.get('eth_dominance', 'N/A')}")
    else:
        lines.append("  (unavailable)")
    lines.append("")

    # Social data
    sd = r.get("social_data", {})
    lines.append("── Social Pulse ──")
    if sd and "_error" not in sd:
        lines.append(f"  Asset              : {sd.get('name')} ({sd.get('symbol')})")
        lines.append(f"  Price              : {_fmt(sd.get('price'))}")
        lines.append(f"  24h Price Change   : {_pct(sd.get('price_change_24h'))}")
        lines.append(f"  7d Price Change    : {_pct(sd.get('price_change_7d'))}")
        lines.append(f"  Twitter Followers  : {_fmt(sd.get('twitter_followers'), '')}")
        lines.append(f"  Reddit Subs        : {_fmt(sd.get('reddit_subscribers'), '')}")
        lines.append(f"  Telegram Users     : {_fmt(sd.get('telegram_users'), '')}")
    else:
        lines.append(f"  {sd.get('_error', '(unavailable)')}")
    lines.append("")

    # Funding
    fd = r.get("funding", {})
    lines.append("── Perp Markets / Sentiment Proxy ──")
    if fd and "_error" not in fd:
        lines.append(f"  Perp Markets Found : {fd.get('perp_tickers_found', 0)}")
        for m in fd.get("top_perp_markets", [])[:3]:
            lines.append(f"    {m.get('exchange', '?')}: {m.get('pair')} vol={_fmt(m.get('converted_volume'))}")
        if fd.get("note"):
            lines.append(f"  {fd['note']}")
    else:
        lines.append("  (unavailable)")
    lines.append("")

    # Trending
    tr = r.get("trending", [])
    if tr and "_error" not in tr:
        lines.append("── Trending Coins (Social Volume) ──")
        for i, t in enumerate(tr[:10], 1):
            lines.append(f"  {i}. {t.get('name')} ({t.get('symbol')}) — Rank #{t.get('market_cap_rank', '?')}")
        lines.append("")

    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────────

def list_coins():
    """Print all known coin mappings."""
    print("Known Coin IDs (CoinGecko):")
    for name, cid in sorted(COIN_IDS.items()):
        print(f"  {name:15s} -> {cid}")
    print()
    print("Use --coin <id> to fetch data for any coin.")


def main():
    parser = argparse.ArgumentParser(
        description="Pheme — Sentiment Data Fetcher v3.0",
    )
    parser.add_argument("--coin", "-c", default="bitcoin", help="Coin name or ID (default: bitcoin)")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    parser.add_argument("--list-coins", action="store_true", help="List known coin ID mappings")
    args = parser.parse_args()

    if args.list_coins:
        list_coins()
        return

    report = build_report(coin=args.coin, json_mode=args.json)
    print(report)


if __name__ == "__main__":
    main()
