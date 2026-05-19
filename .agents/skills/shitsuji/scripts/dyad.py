#!/usr/bin/env python3
"""Plutchik 24-dyad lookup module + family taxonomy.

The Plutchik wheel has 8 primary emotions arranged on a circle. Combining
two adjacent emotions yields a "dyad" — a richer compound emotion that
captures nuance the primary alone cannot. There are 24 dyads:

    distance 1 → primary dyad   (8 entries: love, optimism, ...)
    distance 2 → secondary dyad (8 entries: hope, anxiety, ...)
    distance 3 → tertiary dyad  (8 entries: dominance, sentimentality, ...)
    distance 4 → antithesis     (NOT a dyad — contradictory)

This module is the single source of dyad truth for the Skill. The
markdown reference at prompts/dyad-table.md is the human-readable
mirror; the two MUST stay in sync.

Public API:
    WHEEL                       — list of 8 primary emotion names in wheel order
    wheel_distance(a, b)        — int distance on circle (0-4); -1 if invalid
    dyad_name(p, s)             — str dyad name, or None if no dyad
    is_antithesis(p, s)         — True if pair is on opposite ends
    dyad_family(name)           — "approach" | "withdraw" | "aggressive" | "ambivalent" | None
    all_dyads()                 — flat dict {(a, b): name} for testing/coverage
"""

from __future__ import annotations

from typing import Optional

# 8 primary emotions in Plutchik wheel order. Index = wheel position.
WHEEL: list[str] = [
    "joy", "trust", "fear", "surprise",
    "sadness", "disgust", "anger", "anticipation",
]
WHEEL_IDX: dict[str, int] = {name: i for i, name in enumerate(WHEEL)}

# Dyads keyed by (primary, secondary) in canonical (lower-index-first) order.
# We'll register both orders in a flat lookup dict at module load.
_DYAD_DEFS = {
    1: {  # primary dyads — adjacent on wheel
        ("joy", "trust"): "love",
        ("trust", "fear"): "submission",
        ("fear", "surprise"): "awe",
        ("surprise", "sadness"): "disapproval",
        ("sadness", "disgust"): "remorse",
        ("disgust", "anger"): "contempt",
        ("anger", "anticipation"): "aggressiveness",
        ("anticipation", "joy"): "optimism",
    },
    2: {  # secondary dyads — one apart
        ("joy", "fear"): "guilt",
        ("trust", "surprise"): "curiosity",
        ("fear", "sadness"): "despair",
        ("surprise", "disgust"): "unbelief",
        ("sadness", "anger"): "envy",
        ("disgust", "anticipation"): "cynicism",
        ("anger", "joy"): "pride",
        ("anticipation", "trust"): "hope",
    },
    3: {  # tertiary dyads — two apart
        ("joy", "surprise"): "delight",
        ("trust", "sadness"): "sentimentality",
        ("fear", "disgust"): "shame",
        ("surprise", "anger"): "outrage",
        ("sadness", "anticipation"): "pessimism",
        ("disgust", "joy"): "morbidness",
        ("anger", "trust"): "dominance",
        ("anticipation", "fear"): "anxiety",
    },
}

# Build flat order-independent lookup at module load.
_LOOKUP: dict[frozenset[str], str] = {}
for _distance, _table in _DYAD_DEFS.items():
    for (_a, _b), _name in _table.items():
        _LOOKUP[frozenset({_a, _b})] = _name


# Tone-relevant family categorization. Used by compute_mood to suggest
# tone modifiers when a dyad cluster appears in recent history.
_FAMILIES: dict[str, str] = {
    # approach: positively-valenced engagement, lean-in
    "love": "approach",
    "optimism": "approach",
    "hope": "approach",
    "delight": "approach",
    "pride": "approach",
    "dominance": "approach",
    # withdraw: pulling back, vulnerability, deference
    "submission": "withdraw",
    "awe": "withdraw",
    "disapproval": "withdraw",
    "remorse": "withdraw",
    "despair": "withdraw",
    "shame": "withdraw",
    "anxiety": "withdraw",
    "guilt": "withdraw",
    # aggressive: outward-directed negative valence
    "contempt": "aggressive",
    "aggressiveness": "aggressive",
    "envy": "aggressive",
    "outrage": "aggressive",
    "cynicism": "aggressive",
    # ambivalent: mixed-valence, observational
    "sentimentality": "ambivalent",
    "morbidness": "ambivalent",
    "unbelief": "ambivalent",
    "curiosity": "ambivalent",
    "pessimism": "ambivalent",
}


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def wheel_distance(a: str, b: str) -> int:
    """Return distance between two primary emotions on the 8-point wheel.

    0 = same, 1 = adjacent, ..., 4 = opposite (antithesis).
    Returns -1 if either input is not a recognized primary emotion.
    """
    if a not in WHEEL_IDX or b not in WHEEL_IDX:
        return -1
    diff = abs(WHEEL_IDX[a] - WHEEL_IDX[b])
    return min(diff, len(WHEEL) - diff)


def dyad_name(primary: str, secondary: str) -> Optional[str]:
    """Return the dyad name for the (primary, secondary) pair, else None.

    Returns None when:
        - either input is "neutral"
        - primary == secondary
        - the pair is an antithesis (distance 4)
        - either input is unrecognized
    """
    if primary == "neutral" or secondary == "neutral":
        return None
    if primary == secondary:
        return None
    if primary not in WHEEL_IDX or secondary not in WHEEL_IDX:
        return None
    if wheel_distance(primary, secondary) == 4:
        return None
    return _LOOKUP.get(frozenset({primary, secondary}))


def is_antithesis(primary: str, secondary: str) -> bool:
    """True iff the pair sits on opposite ends of the wheel (distance 4).

    Antithesis pairs (joy↔sadness, trust↔disgust, fear↔anger,
    surprise↔anticipation) are contradictory and should never appear
    together in a single AI emotion entry.
    """
    if primary not in WHEEL_IDX or secondary not in WHEEL_IDX:
        return False
    return wheel_distance(primary, secondary) == 4


def dyad_family(name: str) -> Optional[str]:
    """Return the tone-relevant family for a dyad name.

    "approach"   — lean-in, positively engaged
    "withdraw"   — pulling back, vulnerability, deference
    "aggressive" — outward-directed negative valence
    "ambivalent" — mixed valence, observational

    Returns None for unrecognized names.
    """
    return _FAMILIES.get(name)


def all_dyads() -> dict[tuple[str, str], str]:
    """Return a flat dict of every (primary, secondary) → dyad name pair.

    For test coverage: ensures the canonical mapping is the only source.
    """
    out: dict[tuple[str, str], str] = {}
    for table in _DYAD_DEFS.values():
        for pair, name in table.items():
            out[pair] = name
    return out


# ---------------------------------------------------------------------------
# CLI smoke (mostly for human inspection)
# ---------------------------------------------------------------------------


def _main() -> None:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--lookup", nargs=2, metavar=("PRIMARY", "SECONDARY"),
                    help="resolve a (primary, secondary) pair to a dyad name")
    ap.add_argument("--all", action="store_true",
                    help="dump the full dyad table as JSON")
    ap.add_argument("--family", metavar="DYAD_NAME",
                    help="print the family of a given dyad name")
    args = ap.parse_args()

    if args.lookup:
        p, s = args.lookup
        d = wheel_distance(p, s)
        name = dyad_name(p, s)
        out = {
            "primary": p,
            "secondary": s,
            "wheel_distance": d,
            "dyad": name,
            "is_antithesis": is_antithesis(p, s),
            "family": dyad_family(name) if name else None,
        }
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    if args.all:
        out = [
            {"primary": a, "secondary": b, "dyad": n, "family": dyad_family(n)}
            for (a, b), n in all_dyads().items()
        ]
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    if args.family:
        fam = dyad_family(args.family)
        if fam is None:
            print(f"unknown dyad: {args.family}", file=sys.stderr)
            sys.exit(2)
        print(fam)
        return

    ap.print_help()


if __name__ == "__main__":
    _main()
