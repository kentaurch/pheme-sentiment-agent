#!/usr/bin/env python3
"""
narrative_lifecycle.py — Narrative Velocity Analyzer for Pheme

Analyzes narrative velocity — acceleration, peak, plateau, fade.
Takes topic mentions + volume over time, fits a curve to determine
the narrative lifecycle stage.

Usage:
    python3 narrative_lifecycle.py --topic "AI tokens"
    python3 narrative_lifecycle.py --topic "Bitcoin ETF" --json
    python3 narrative_lifecycle.py --simulate --days 90
"""

import argparse
import json
import os
import sys
import time
import functools
import math
import random
from datetime import datetime, timezone, timedelta
from collections import deque

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

NARRATIVE_STAGES = [
    "innovation",
    "early_adoption",
    "peak_hype",
    "plateau",
    "fade",
]

# Typical velocity profiles for each stage (normalized 0-1)
STAGE_PROFILES = {
    "innovation":        {"velocity": 0.2, "acceleration": 0.3,  "volume": 0.1},
    "early_adoption":    {"velocity": 0.4, "acceleration": 0.6,  "volume": 0.3},
    "peak_hype":         {"velocity": 0.9, "acceleration": 0.1,  "volume": 1.0},
    "plateau":           {"velocity": 0.5, "acceleration": -0.2, "volume": 0.7},
    "fade":              {"velocity": 0.1, "acceleration": -0.5, "volume": 0.2},
}

# ── Time Series & Curve Fitting ─────────────────────────────────────────────────


def compute_velocity(volumes):
    """Compute velocity (first derivative) from a volume time series."""
    if len(volumes) < 2:
        return 0.0
    # Simple finite difference: average of last few deltas
    deltas = [volumes[i] - volumes[i-1] for i in range(1, len(volumes))]
    # Weight recent changes more
    weights = [(i+1) / sum(range(1, len(deltas)+1)) for i in range(len(deltas))]
    return sum(d * w for d, w in zip(deltas, weights))


def compute_acceleration(volumes):
    """Compute acceleration (second derivative) from a volume time series."""
    if len(volumes) < 3:
        return 0.0
    deltas = [volumes[i] - volumes[i-1] for i in range(1, len(volumes))]
    if len(deltas) < 2:
        return 0.0
    accels = [deltas[i] - deltas[i-1] for i in range(1, len(deltas))]
    return sum(accels) / len(accels)


def compute_momentum(volumes):
    """Compute momentum direction and strength."""
    if len(volumes) < 5:
        return "neutral", 0.0
    recent = volumes[-5:]
    old = volumes[-10:-5] if len(volumes) >= 10 else volumes[:len(recent)]
    if len(old) < len(recent):
        old = [volumes[0]] * (len(recent) - len(old)) + old

    avg_recent = sum(recent) / len(recent)
    avg_old = sum(old) / len(old)
    diff = avg_recent - avg_old

    if diff > 0.15 * max(volumes):
        return "accelerating", diff
    elif diff < -0.15 * max(volumes):
        return "decelerating", abs(diff)
    elif diff > 0.05 * max(volumes):
        return "rising", diff
    elif diff < -0.05 * max(volumes):
        return "falling", abs(diff)
    else:
        return "stable", abs(diff)


def determine_stage(volumes, mentions_data=None):
    """
    Determine which narrative stage the data represents.
    Uses velocity, acceleration, and volume patterns.
    """
    if not volumes or len(volumes) < 3:
        return {
            "stage": "unknown",
            "velocity_score": 0,
            "acceleration": 0,
            "momentum_direction": "neutral",
            "confidence": "low",
        }

    # Normalize volumes to 0-1
    v_max = max(volumes) if max(volumes) > 0 else 1
    normalized = [v / v_max for v in volumes]

    vel = compute_velocity(normalized)
    acc = compute_acceleration(normalized)
    current_vol = normalized[-1] if normalized else 0
    momentum_dir, momentum_strength = compute_momentum(normalized)

    # Score against each stage profile
    stage_scores = {}
    for stage, profile in STAGE_PROFILES.items():
        score = 0
        # Compare velocity
        score += 1 - abs(vel - profile["velocity"])
        # Compare acceleration
        score += 1 - abs(acc - profile["acceleration"])
        # Compare volume level
        score += 1 - abs(current_vol - profile["volume"])
        stage_scores[stage] = score / 3  # average of 3 components

    # Best match
    best_stage = max(stage_scores, key=stage_scores.get)
    best_score = stage_scores[best_stage]

    # Confidence
    sorted_scores = sorted(stage_scores.values(), reverse=True)
    if len(sorted_scores) > 1 and (sorted_scores[0] - sorted_scores[1]) > 0.2:
        confidence = "high"
    elif len(sorted_scores) > 1 and (sorted_scores[0] - sorted_scores[1]) > 0.1:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "stage": best_stage,
        "stage_confidence": round(best_score, 3),
        "all_stage_scores": {k: round(v, 3) for k, v in stage_scores.items()},
        "velocity_score": round(vel, 4),
        "acceleration": round(acc, 4),
        "momentum_direction": momentum_dir,
        "momentum_strength": round(momentum_strength, 4),
        "current_volume_percent": round(current_vol * 100, 1),
        "confidence": confidence,
    }


# ── Simulated Data (for testing / demo) ─────────────────────────────────────────


def generate_simulated_volumes(days=90, pattern="peak"):
    """Generate a simulated volume time series for testing."""
    volumes = []
    base = random.uniform(10, 50)

    for i in range(days):
        t = i / days  # 0 to 1

        if pattern == "innovation":
            # Low volume, slowly rising
            vol = base * (0.5 + 0.5 * t) + random.gauss(0, base * 0.1)
        elif pattern == "early_adoption":
            # Accelerating growth
            vol = base * (0.3 + 1.7 * t ** 2) + random.gauss(0, base * 0.15)
        elif pattern == "peak_hype":
            # Sharp rise then plateau
            if t < 0.6:
                vol = base * (0.2 + 3.0 * t)
            else:
                vol = base * (2.0 - 1.0 * (t - 0.6) / 0.4)
            vol += random.gauss(0, base * 0.2)
        elif pattern == "plateau":
            # Rose, now flat
            rise = 2.0 * min(t * 2, 1.0)
            vol = base * (0.3 + rise) + random.gauss(0, base * 0.1)
        elif pattern == "fade":
            # Rose then declining
            if t < 0.3:
                vol = base * (0.5 + 3.0 * t)
            else:
                vol = base * (1.4 - 1.2 * (t - 0.3) / 0.7)
            vol += random.gauss(0, base * 0.15)
        else:
            vol = base + random.gauss(0, base * 0.2)

        volumes.append(max(0, vol))

    return volumes


# ── Report Builder ──────────────────────────────────────────────────────────────


def build_lifecycle_report(topic=None, simulated=False, sim_pattern="peak_hype", sim_days=90, json_mode=False):
    """Build a narrative lifecycle analysis report."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if simulated:
        volumes = generate_simulated_volumes(days=sim_days, pattern=sim_pattern)
        mentions_count = max(volumes) if volumes else 0
        topic = topic or f"simulated_{sim_pattern}"
    else:
        # Without real data feed, use simulation as demo
        volumes = generate_simulated_volumes(days=60, pattern="early_adoption")
        mentions_count = max(volumes) if volumes else 0

    analysis = determine_stage(volumes)

    report = {
        "timestamp": timestamp,
        "topic": topic or "unspecified",
        "data_points": len(volumes),
        "peak_volume": max(volumes) if volumes else 0,
        "current_volume": volumes[-1] if volumes else 0,
        "analysis": analysis,
        "note": "Using simulated volume data. Integrate with social_scraper.py for real data.",
    }

    if json_mode:
        return json.dumps(report, indent=2)

    return _format_human_report(report)


def _format_human_report(r):
    """Format the lifecycle report for human reading."""
    a = r.get("analysis", {})

    lines = []
    lines.append("Pheme Narrative Lifecycle Analyzer")
    lines.append(f"Generated: {r.get('timestamp', 'unknown')}")
    lines.append(f"Topic: {r.get('topic', 'unknown')}")
    lines.append("")

    lines.append("── Narrative Stage ──")
    lines.append(f"  Stage              : {a.get('stage', 'unknown').replace('_', ' ').upper()}")
    lines.append(f"  Confidence         : {a.get('confidence', 'low').upper()} ({a.get('stage_confidence', 0):.1%})")
    lines.append("")

    lines.append("── Velocity Metrics ──")
    lines.append(f"  Velocity Score     : {a.get('velocity_score', 0):+.4f}")
    lines.append(f"  Acceleration       : {a.get('acceleration', 0):+.4f}")
    lines.append(f"  Momentum Direction : {a.get('momentum_direction', 'neutral').upper()}")
    lines.append(f"  Momentum Strength  : {a.get('momentum_strength', 0):.4f}")
    lines.append(f"  Volume Position    : {a.get('current_volume_percent', 0)}% of peak")
    lines.append("")

    lines.append("── Stage Breakdown ──")
    stage_scores = a.get("all_stage_scores", {})
    for stage, score in sorted(stage_scores.items(), key=lambda x: -x[1]):
        bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
        lines.append(f"  {stage:20s} [{bar}] {score:.1%}")
    lines.append("")

    lines.append(f"Note: {r.get('note', '')}")

    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Pheme — Narrative Lifecycle Analyzer v3.0",
    )
    parser.add_argument("--topic", "-t", help="Narrative topic to analyze")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    parser.add_argument("--simulate", action="store_true", help="Use simulated data")
    parser.add_argument("--pattern", choices=list(STAGE_PROFILES.keys()), default="peak_hype",
                        help="Simulation pattern (default: peak_hype)")
    parser.add_argument("--days", type=int, default=90, help="Days of data for simulation")
    args = parser.parse_args()

    report = build_lifecycle_report(
        topic=args.topic,
        simulated=args.simulate,
        sim_pattern=args.pattern,
        sim_days=args.days,
        json_mode=args.json,
    )
    print(report)


if __name__ == "__main__":
    main()
