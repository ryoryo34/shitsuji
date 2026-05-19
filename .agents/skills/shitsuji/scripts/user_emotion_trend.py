#!/usr/bin/env python3
"""Aggregate the user emotion trend from HISTORY.jsonl.

In Mode B, every entry in HISTORY.jsonl carries:
- top-level VAD = the AI's reactive emotion (drives mood/tone)
- ``user_emotion`` sub-object = the user's observed emotion (analytics-only)

This script reads the ``user_emotion`` sub-objects and produces a summary:

  * count of entries with user_emotion data
  * VAD mean / min / max per dimension
  * primary-emotion frequency (top 5)
  * a sparkline-like timeline of user valence (most recent 30 entries)

Output: human-readable text on stdout. JSON mode available with ``--json``.

Usage:
    user_emotion_trend.py
    user_emotion_trend.py --json
    user_emotion_trend.py --window 100
    SHITSUJI_DATA_DIR=/path/to/data user_emotion_trend.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SHITSUJI_DATA_DIR") or (SKILL_DIR / "data"))
HISTORY_FILE = DATA_DIR / "HISTORY.jsonl"

SPARK_CHARS = "▁▂▃▄▅▆▇█"


def read_user_entries(window: int | None) -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    entries: list[dict] = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        ue = obj.get("user_emotion")
        if not isinstance(ue, dict):
            continue
        entries.append({"ts": obj.get("ts"), **ue})
    if window is not None and window > 0:
        entries = entries[-window:]
    return entries


def stat(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": 0.0, "min": 0.0, "max": 0.0}
    return {
        "n": len(values),
        "mean": round(sum(values) / len(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def sparkline(values: list[float]) -> str:
    if not values:
        return ""
    bands = len(SPARK_CHARS)
    return "".join(
        SPARK_CHARS[min(bands - 1, max(0, int((v + 1.0) / 2.0 * (bands - 1))))]
        for v in values
    )


def render_text(entries: list[dict]) -> str:
    if not entries:
        return "No entries with user_emotion sub-object found in HISTORY.jsonl."

    valences = [float(e.get("valence", 0.0)) for e in entries]
    arousals = [float(e.get("arousal", 0.0)) for e in entries]
    dominances = [float(e.get("dominance", 0.0)) for e in entries]
    primaries = [str(e.get("primary", "neutral")) for e in entries]

    s_v = stat(valences)
    s_a = stat(arousals)
    s_d = stat(dominances)
    counter = Counter(primaries).most_common(5)

    lines = [
        "shitsuji user-emotion trend (Mode B analytics)",
        f"  source : {HISTORY_FILE}",
        f"  entries: {len(entries)}",
        "",
        "VAD statistics over user_emotion (range [-1, 1]):",
        f"  valence   mean={s_v['mean']:+.3f}  min={s_v['min']:+.3f}  max={s_v['max']:+.3f}",
        f"  arousal   mean={s_a['mean']:+.3f}  min={s_a['min']:+.3f}  max={s_a['max']:+.3f}",
        f"  dominance mean={s_d['mean']:+.3f}  min={s_d['min']:+.3f}  max={s_d['max']:+.3f}",
        "",
        "Top user emotions (primary):",
    ]
    for label, count in counter:
        pct = count / len(entries) * 100
        lines.append(f"  {label:<14s} {count:>4d}  ({pct:5.1f}%)")

    last30 = valences[-30:]
    if last30:
        lines.append("")
        lines.append(f"Valence sparkline (last {len(last30)}):  {sparkline(last30)}")

    return "\n".join(lines)


def render_json(entries: list[dict]) -> str:
    valences = [float(e.get("valence", 0.0)) for e in entries]
    arousals = [float(e.get("arousal", 0.0)) for e in entries]
    dominances = [float(e.get("dominance", 0.0)) for e in entries]
    primaries = [str(e.get("primary", "neutral")) for e in entries]

    out = {
        "source": str(HISTORY_FILE),
        "entries": len(entries),
        "valence": stat(valences),
        "arousal": stat(arousals),
        "dominance": stat(dominances),
        "primary_top5": Counter(primaries).most_common(5),
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    entries = read_user_entries(args.window)
    if args.json:
        print(render_json(entries))
    else:
        print(render_text(entries))


if __name__ == "__main__":
    main()
