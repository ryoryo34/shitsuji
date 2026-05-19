#!/usr/bin/env python3
"""Aggregate emotion impulse score per primary from HISTORY.jsonl.

Per Boeda 2021 (Square Enix Wonder, CEDEC) the balance metric for emotion
systems is:

    影響スコア = Σ(intensity × duration)

In shitsuji every HISTORY entry corresponds to one conversation turn,
so duration is implicit (1 turn). This gives a turn-normalized impulse:

    impulse(primary) = Σ_{turns with this primary} intensity

Higher impulse = the primary contributed more cumulative pull on the mood
EMA. Useful for spotting flattening (one primary dominates), runaway
trajectories (one primary accumulating disproportionate impulse over a
short window), or asymmetric persona expression.

Output: human-readable text on stdout. JSON mode available with ``--json``.

Usage:
    emotion_impulse.py
    emotion_impulse.py --json
    emotion_impulse.py --window 100
    SHITSUJI_DATA_DIR=/path/to/data emotion_impulse.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SHITSUJI_DATA_DIR") or (SKILL_DIR / "data"))
HISTORY_FILE = DATA_DIR / "HISTORY.jsonl"


def read_ai_entries(window: int | None) -> list[dict]:
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
        if "primary" not in obj or "intensity" not in obj:
            continue
        entries.append(obj)
    if window is not None and window > 0:
        entries = entries[-window:]
    return entries


def aggregate(entries: list[dict]) -> dict[str, dict]:
    """Aggregate impulse and turn-count per primary."""
    agg: dict[str, dict] = defaultdict(
        lambda: {"turns": 0, "impulse": 0.0, "max_intensity": 0.0}
    )
    for e in entries:
        primary = str(e.get("primary", "neutral"))
        intensity = float(e.get("intensity", 0.0))
        agg[primary]["turns"] += 1
        agg[primary]["impulse"] += intensity
        if intensity > agg[primary]["max_intensity"]:
            agg[primary]["max_intensity"] = intensity
    return dict(agg)


def render_text(entries: list[dict]) -> str:
    if not entries:
        return "No AI emotion entries found in HISTORY.jsonl."

    agg = aggregate(entries)
    total_turns = len(entries)
    total_impulse = sum(v["impulse"] for v in agg.values())

    ranked = sorted(agg.items(), key=lambda kv: kv[1]["impulse"], reverse=True)

    lines = [
        "shitsuji emotion impulse (Boeda 2021 balance metric)",
        f"  source : {HISTORY_FILE}",
        f"  entries: {total_turns} turns",
        f"  total  : impulse={total_impulse:.2f} (mean intensity per turn = {total_impulse / total_turns:.3f})",
        "",
        f"{'primary':<14s} {'turns':>6s} {'%turns':>7s} {'impulse':>9s} {'%impulse':>9s} {'mean':>7s} {'max':>6s}",
        "-" * 64,
    ]
    for primary, v in ranked:
        turns = v["turns"]
        impulse = v["impulse"]
        mean = impulse / turns if turns else 0.0
        pct_turns = turns / total_turns * 100 if total_turns else 0.0
        pct_impulse = impulse / total_impulse * 100 if total_impulse else 0.0
        lines.append(
            f"{primary:<14s} {turns:>6d} {pct_turns:>6.1f}% "
            f"{impulse:>9.2f} {pct_impulse:>8.1f}% "
            f"{mean:>7.3f} {v['max_intensity']:>6.3f}"
        )

    if ranked:
        top_primary, top = ranked[0]
        top_share = top["impulse"] / total_impulse if total_impulse else 0.0
        if top_share > 0.6:
            lines.append("")
            lines.append(
                f"⚠ Flattening watch: '{top_primary}' carries {top_share:.0%} of total impulse — "
                "check whether this reflects intentional persona or affective collapse."
            )

    return "\n".join(lines)


def render_json(entries: list[dict]) -> str:
    agg = aggregate(entries)
    total_turns = len(entries)
    total_impulse = sum(v["impulse"] for v in agg.values())
    out = {
        "source": str(HISTORY_FILE),
        "entries": total_turns,
        "total_impulse": round(total_impulse, 3),
        "by_primary": {
            primary: {
                "turns": v["turns"],
                "impulse": round(v["impulse"], 3),
                "mean_intensity": round(v["impulse"] / v["turns"], 3) if v["turns"] else 0.0,
                "max_intensity": round(v["max_intensity"], 3),
            }
            for primary, v in agg.items()
        },
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--window", type=int, default=None, help="Last N turns only")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args()

    entries = read_ai_entries(args.window)
    if args.json:
        print(render_json(entries))
    else:
        print(render_text(entries))


if __name__ == "__main__":
    main()
