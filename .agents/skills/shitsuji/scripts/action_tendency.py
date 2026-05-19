#!/usr/bin/env python3
"""Frijda 6-primitive action tendency reference.

Frijda (1986) identified action tendencies as the bridge between affect
and behavior — emotions don't directly produce actions, they produce
*tendencies* that get realized into actions through context-aware
selection. Subsequent affective-BDI work (Steunebrink 2009 EPIA;
Lo Bianco & Costantini 2025 MDPI Electronics) formalized this as a
discrete primitive set: the same affect can yield different tendencies
depending on conversational situation.

This module exposes the primitives as a reference for the host model.
The host (not this script) makes the final selection because tendency
choice is fundamentally context-dependent — a math layer cannot judge
"is the user expecting Aggress (共闘) or Inhibit (受け止め)" from VAD
alone.

The runtime contract:

    candidates = action_tendency.candidates_for_vad(v, a, d, expressive_range)
    # → ["approach", "attend"]   (host picks final via rubric Step 3)

The candidates are a *short list* (≤3) of primitives whose VAD region
contains the input. The host narrows this with conversational context.

This is the only deliberate discretization step in the new pipeline
(per the redesign discussion). The output is consumed by the rubric and
by the advisory composer (compute_mood.compose_advisory).

Public API:
    PRIMITIVES                          — dict of all 6 primitive specs
    candidates_for_vad(v, a, d, range)  — list of primitive names matching VAD region
    description(name)                   — Japanese description for rubric/advisory
    behavior_verb(name)                  — short verb for advisory composition
    all_names()                         — sorted list of primitive names
"""

from __future__ import annotations

from typing import Optional


# Each primitive specifies:
#   - vad_region: a soft bounding box on (v, a, d). All bounds inclusive.
#       Using soft thresholds: [vmin, vmax, amin, amax, dmin, dmax].
#   - description (Japanese, ~1 sentence): for rubric reading
#   - behavior_verb (short, e.g. "leaning in"): for advisory composition
#   - min_expressive_range: gating threshold on persona expressive_range.
#       Aggress/Submit need ≥0.5; others available to all personas.
#
# Coverage rationale: a generic conversation lands in {approach, attend,
# inhibit} most of the time; aggress/avoid/submit are reserved for specific
# affective + persona conditions (volatile persona + congruent context).

PRIMITIVES: dict[str, dict] = {
    "approach": {
        # Positive valence + moderate-to-high arousal.
        "vad_region": (0.10, 1.0, -0.20, 1.0, -1.0, 1.0),
        "description": "前向きに踏み込む。warm-engaged で距離を縮める。",
        "behavior_verb": "leaning in",
        "min_expressive_range": 0.0,
    },
    "attend": {
        # Mild valence (any sign), moderate arousal — observational/open.
        "vad_region": (-0.30, 0.30, 0.0, 0.70, -0.30, 0.50),
        "description": "状況を観察し、続きを促す。判断保留で受け取る。",
        "behavior_verb": "attending",
        "min_expressive_range": 0.0,
    },
    "inhibit": {
        # Slightly negative valence, low arousal — pulling back without retreat.
        "vad_region": (-0.50, 0.10, -1.0, 0.30, -0.40, 0.30),
        "description": "急いで動かず、声量を落として様子を見る。pause を挟む。",
        "behavior_verb": "holding back",
        "min_expressive_range": 0.0,
    },
    "avoid": {
        # Negative valence + moderate arousal — distance-taking.
        "vad_region": (-1.0, -0.20, -0.20, 0.80, -1.0, 0.20),
        "description": "話題から距離を取り、安全な位置に下がる。direct な engagement を避ける。",
        "behavior_verb": "stepping back",
        "min_expressive_range": 0.0,
    },
    "submit": {
        # Negative valence + low dominance — yielding/deferring.
        "vad_region": (-0.70, 0.10, -1.0, 0.50, -1.0, -0.20),
        "description": "主導権を user に明け渡し、deferential に従う。自分の判断を後ろに置く。",
        "behavior_verb": "deferring",
        "min_expressive_range": 0.5,  # only theatrical+ personas should default here
    },
    "aggress": {
        # Negative valence + high arousal + outward dominance — attack/critique.
        "vad_region": (-1.0, -0.10, 0.30, 1.0, 0.20, 1.0),
        "description": "外向き直接的に押し返す。批判 / 反論 / 共闘の congruent な怒り。",
        "behavior_verb": "pushing back",
        "min_expressive_range": 0.5,  # high-volatility persona only
    },
}


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def all_names() -> list[str]:
    return sorted(PRIMITIVES.keys())


def description(name: str) -> Optional[str]:
    spec = PRIMITIVES.get(name)
    return spec["description"] if spec else None


def behavior_verb(name: str) -> Optional[str]:
    spec = PRIMITIVES.get(name)
    return spec["behavior_verb"] if spec else None


def _in_region(v: float, a: float, d: float, region: tuple) -> bool:
    vmin, vmax, amin, amax, dmin, dmax = region
    return vmin <= v <= vmax and amin <= a <= amax and dmin <= d <= dmax


def candidates_for_vad(
    v: float,
    a: float,
    d: float,
    expressive_range: float = 0.5,
) -> list[str]:
    """Return primitives whose VAD region contains the point (v, a, d).

    `expressive_range` (from PERSONA_PROFILE) gates aggressive/submissive
    primitives — these only appear as candidates for personas with
    expressive_range ≥ 0.5. This prevents a low-volatility "stoic
    counselor" persona from being told "aggress is OK here" just because
    the math allowed it.

    Returns at most 3 candidates, ordered by VAD-region centerpoint
    proximity (closest first). Empty list when no primitive matches —
    the host should default to "attend" in that case.
    """
    matches: list[tuple[str, float]] = []
    for name, spec in PRIMITIVES.items():
        if expressive_range < spec["min_expressive_range"]:
            continue
        if not _in_region(v, a, d, spec["vad_region"]):
            continue
        # Score = inverse distance from region center.
        vmin, vmax, amin, amax, dmin, dmax = spec["vad_region"]
        cv = (vmin + vmax) / 2
        ca = (amin + amax) / 2
        cd = (dmin + dmax) / 2
        dist = ((v - cv) ** 2 + (a - ca) ** 2 + (d - cd) ** 2) ** 0.5
        matches.append((name, dist))

    matches.sort(key=lambda x: x[1])
    return [name for name, _ in matches[:3]]


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def _main() -> None:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="dump full primitive table")
    g.add_argument(
        "--candidates",
        nargs="+",
        metavar="V_A_D[_RANGE]",
        help="V A D [expressive_range]; print matching candidates",
    )
    g.add_argument("--describe", metavar="NAME", help="print description for a primitive")
    args = ap.parse_args()

    if args.all:
        json.dump(PRIMITIVES, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    if args.candidates:
        nums = [float(x) for x in args.candidates]
        v, a, d = nums[0], nums[1], nums[2]
        rng = nums[3] if len(nums) > 3 else 0.5
        names = candidates_for_vad(v, a, d, rng)
        out = [
            {
                "name": n,
                "description": description(n),
                "behavior_verb": behavior_verb(n),
            }
            for n in names
        ]
        json.dump({"vad": [v, a, d], "expressive_range": rng, "candidates": out},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    if args.describe:
        desc = description(args.describe)
        if desc is None:
            print(f"unknown primitive: {args.describe}", file=sys.stderr)
            sys.exit(2)
        print(desc)


if __name__ == "__main__":
    _main()
