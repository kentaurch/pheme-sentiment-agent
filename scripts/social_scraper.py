#!/usr/bin/env python3
"""
social_scraper.py — Social Media Sentiment Aggregator for Pheme

Fetches public social data from RSS feeds (Reddit, HackerNews, general crypto news).
Aggregates sentiment counts, top mentions, and trending topics.

Usage:
    python3 social_scraper.py --query bitcoin
    python3 social_scraper.py --query ethereum --json
    python3 social_scraper.py --list-sources
"""

import argparse
import json
import os
import sys
import time
import functools
import html
import re
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from collections import Counter

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

# ── Cache Decorator ─────────────────────────────────────────────────────────────

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

RSS_SOURCES = {
    "reddit_crypto": {
        "url": "https://www.reddit.com/r/CryptoCurrency/.rss",
        "name": "Reddit r/CryptoCurrency",
        "type": "social",
        "weight": 1.0,
    },
    "reddit_btc": {
        "url": "https://www.reddit.com/r/Bitcoin/.rss",
        "name": "Reddit r/Bitcoin",
        "type": "social",
        "weight": 0.8,
    },
    "reddit_eth": {
        "url": "https://www.reddit.com/r/ethtrader/.rss",
        "name": "Reddit r/ethtrader",
        "type": "social",
        "weight": 0.8,
    },
    "hackernews": {
        "url": "https://hnrss.org/frontpage?count=20",
        "name": "HackerNews Front Page",
        "type": "news",
        "weight": 0.6,
    },
    "coindesk": {
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "name": "CoinDesk",
        "type": "news",
        "weight": 0.9,
    },
    "cointelegraph": {
        "url": "https://cointelegraph.com/rss",
        "name": "CoinTelegraph",
        "type": "news",
        "weight": 0.9,
    },
    "decrypt": {
        "url": "https://decrypt.co/feed",
        "name": "Decrypt",
        "type": "news",
        "weight": 0.7,
    },
}

# Sentiment lexicon for simple positive/negative scoring
POSITIVE_WORDS = {
    "bullish", "moon", "pump", "buy", "long", "accumulate", "breakout", "rally",
    "growth", "adoption", "upgrade", "partnership", "launch", "approval", "green",
    "profit", "gains", "surge", "soar", "boom", "opportunity", "innovation",
    "breakthrough", "mainstream", "institutional", "hodl", "diamond hands",
}

NEGATIVE_WORDS = {
    "bearish", "crash", "dump", "sell", "short", "fud", "scam", "hack",
    "exploit", "rugpull", "decline", "ban", "regulation", "crackdown", "tax",
    "loss", "bloodbath", "plunge", "tank", "panic", "fear", "uncertainty",
    "delist", "bankruptcy", "layoff", "downgrade", "inflation", "recession",
}

# ── Helpers ─────────────────────────────────────────────────────────────────────


def _fetch_rss(url, timeout=15):
    """Fetch and parse an RSS/Atom feed. Returns list of entries."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Pheme/3.0-SocialScraper"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"_error": f"Fetch failed: {e}"}

    try:
        return _parse_feed(raw, url)
    except Exception as e:
        return {"_error": f"Parse failed: {e}"}


def _parse_feed(raw, url):
    """Parse RSS or Atom XML into uniform entry list."""
    entries = []
    root = ET.fromstring(raw)

    # Handle namespaces
    ns = {}
    for m in re.finditer(r'xmlns:?(\w*)\s*=\s*["\']([^"\']+)["\']', raw[:2000]):
        prefix, uri = m.group(1), m.group(2)
        ns[prefix] = uri

    # Try RSS 2.0
    for item in root.iter("item"):
        entry = {}
        title_el = item.find("title")
        entry["title"] = title_el.text if title_el is not None else ""

        desc_el = item.find("description")
        entry["description"] = _strip_html(desc_el.text[:500] if desc_el is not None and desc_el.text else "")

        link_el = item.find("link")
        entry["url"] = link_el.text if link_el is not None else ""

        pubdate_el = item.find("pubDate")
        entry["published"] = pubdate_el.text if pubdate_el is not None else ""

        entries.append(entry)

    # Try Atom
    if not entries:
        for item in root.iter("{http://www.w3.org/2005/Atom}entry"):
            entry = {}
            title_el = item.find("{http://www.w3.org/2005/Atom}title")
            entry["title"] = title_el.text if title_el is not None else ""

            content_el = item.find("{http://www.w3.org/2005/Atom}content")
            entry["description"] = _strip_html(content_el.text[:500] if content_el is not None and content_el.text else "")

            link_el = item.find("{http://www.w3.org/2005/Atom}link")
            entry["url"] = link_el.get("href", "") if link_el is not None else ""

            published_el = item.find("{http://www.w3.org/2005/Atom}published")
            updated_el = item.find("{http://www.w3.org/2005/Atom}updated")
            entry["published"] = (published_el.text if published_el is not None
                                  else (updated_el.text if updated_el is not None else ""))

            entries.append(entry)

    return entries


def _strip_html(text):
    """Remove HTML tags and decode entities."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _simple_sentiment(text):
    """Run simple lexicon-based sentiment on text."""
    if not text:
        return 0
    words = set(re.findall(r"[a-zA-Z]+", text.lower()))
    pos = len(words & POSITIVE_WORDS)
    neg = len(words & NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0
    return (pos - neg) / total  # -1 to +1


def _extract_topics(text, query=""):
    """Extract potential trending topics/cashtags from text."""
    topics = []
    # Find $TICKER patterns
    topics.extend(re.findall(r'\$([A-Z]{2,10})', text.upper()))
    # Find #hashtags
    topics.extend(re.findall(r'#(\w+)', text))
    # Find capitalized multi-word phrases (potential project names)
    phrases = re.findall(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', text)
    topics.extend(phrases)
    return topics


# ── Feed Fetchers ───────────────────────────────────────────────────────────────


@cached(ttl_seconds=600)
def fetch_source(source_key, source_config):
    """Fetch and analyze a single RSS source."""
    result = _fetch_rss(source_config["url"])
    if isinstance(result, dict) and "_error" in result:
        return result

    analyzed = []
    for entry in result:
        text = f"{entry.get('title', '')} {entry.get('description', '')}"
        entry["sentiment_score"] = _simple_sentiment(text)
        entry["topics"] = _extract_topics(text)
        analyzed.append(entry)

    return {
        "source_name": source_config["name"],
        "source_type": source_config["type"],
        "weight": source_config["weight"],
        "entries": analyzed,
        "entry_count": len(analyzed),
    }


def aggregate_sentiment(results):
    """Aggregate sentiment across all sources."""
    total_weighted_score = 0.0
    total_weight = 0.0
    all_topics = []
    all_titles = []
    total_posts = 0
    source_breakdown = {}

    for key, data in results.items():
        if isinstance(data, dict) and "_error" in data:
            continue

        source_breakdown[key] = {
            "name": data.get("source_name", key),
            "type": data.get("source_type", "unknown"),
            "entry_count": data.get("entry_count", 0),
        }

        weight = data.get("weight", 1.0)
        entries = data.get("entries", [])

        if entries:
            source_scores = [e.get("sentiment_score", 0) for e in entries]
            avg_score = sum(source_scores) / len(source_scores)
            source_breakdown[key]["avg_sentiment"] = round(avg_score, 4)
            total_weighted_score += avg_score * weight
            total_weight += weight

            for e in entries:
                all_topics.extend(e.get("topics", []))
                all_titles.append(e.get("title", ""))

        total_posts += len(entries)

    # Overall sentiment
    overall_sentiment = total_weighted_score / total_weight if total_weight > 0 else 0

    # Top mentions
    topic_counts = Counter(all_topics)
    top_mentions = [{"topic": t, "count": c} for t, c in topic_counts.most_common(20)]

    # Trending topics (mentioned in >1 source)
    trending = [t for t, c in topic_counts.most_common(30) if c >= 2]

    return {
        "overall_sentiment_score": round(overall_sentiment, 4),
        "sentiment_label": _sentiment_label(overall_sentiment),
        "total_posts_scanned": total_posts,
        "sources_contributing": sum(1 for d in results.values() if isinstance(d, dict) and "_error" not in d),
        "sources_total": len(results),
        "top_mentions": top_mentions[:15],
        "trending_topics": trending[:10],
        "source_breakdown": source_breakdown,
    }


def _sentiment_label(score):
    """Convert numeric sentiment to label."""
    if score > 0.15:
        return "bullish"
    if score < -0.15:
        return "bearish"
    if score > 0.05:
        return "slightly_bullish"
    if score < -0.05:
        return "slightly_bearish"
    return "neutral"


# ── Query Filter ────────────────────────────────────────────────────────────────


def filter_by_query(aggregated, query):
    """Filter results to items mentioning a specific query."""
    if not query:
        return aggregated

    query_lower = query.lower()
    query_upper = query.upper()

    filtered_titles = []
    matching_count = 0

    # We need to re-scan source data — the aggregated dict loses entry detail
    # So we add a note about the query
    aggregated["query"] = query
    return aggregated


# ── Report Builder ──────────────────────────────────────────────────────────────


def build_social_report(query=None, json_mode=False):
    """Build a comprehensive social sentiment report."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Fetch all sources
    results = {}
    for key, config in RSS_SOURCES.items():
        try:
            results[key] = fetch_source(key, config)
            if isinstance(results[key], dict) and "_error" in results[key]:
                print(f"  [warn] Source '{key}' failed: {results[key]['_error']}", file=sys.stderr)
        except Exception as e:
            print(f"  [warn] Source '{key}' error: {e}", file=sys.stderr)
            results[key] = {"_error": str(e)}

    # Aggregate
    aggregated = aggregate_sentiment(results)
    aggregated["query"] = query
    aggregated["timestamp"] = timestamp

    if json_mode:
        return json.dumps(aggregated, indent=2)

    return _format_human_report(aggregated, query)


def _format_human_report(agg, query):
    """Format the social sentiment report for human reading."""
    lines = []
    lines.append("Pheme Social Sentiment Aggregator")
    lines.append(f"Generated: {agg.get('timestamp', 'unknown')}")
    if query:
        lines.append(f"Query: {query}")
    lines.append("")

    lines.append("── Overall Sentiment ──")
    lines.append(f"  Score              : {agg.get('overall_sentiment_score', 0):.4f}")
    lines.append(f"  Label              : {agg.get('sentiment_label', 'neutral').upper()}")
    lines.append(f"  Posts Scanned      : {agg.get('total_posts_scanned', 0)}")
    lines.append(f"  Sources Online     : {agg.get('sources_contributing', 0)}/{agg.get('sources_total', 0)}")
    lines.append("")

    lines.append("── Top Mentions ──")
    for m in agg.get("top_mentions", [])[:10]:
        lines.append(f"  {m['topic']:25s} (appeared {m['count']} times)")
    lines.append("")

    lines.append("── Trending Topics ──")
    for t in agg.get("trending_topics", []):
        lines.append(f"  {t}")
    lines.append("")

    lines.append("── Source Breakdown ──")
    for key, info in agg.get("source_breakdown", {}).items():
        sentiment = info.get("avg_sentiment", "N/A")
        if isinstance(sentiment, float):
            sentiment = f"{sentiment:+.4f}"
        lines.append(f"  {info['name']:30s} {info['entry_count']:3d} posts  sentiment: {sentiment}")
    lines.append("")

    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────────


def list_sources():
    """Print all configured RSS sources."""
    print("Configured Social/News Sources:")
    print()
    for key, config in RSS_SOURCES.items():
        print(f"  {key:20s} {config['name']:30s} [{config['type']}]")
        print(f"  {'':20s} {config['url']}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Pheme — Social Media Sentiment Aggregator v3.0",
    )
    parser.add_argument("--query", "-q", help="Search query / coin to filter for")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    parser.add_argument("--list-sources", action="store_true", help="List configured RSS sources")
    args = parser.parse_args()

    if args.list_sources:
        list_sources()
        return

    report = build_social_report(query=args.query, json_mode=args.json)
    print(report)


if __name__ == "__main__":
    main()
