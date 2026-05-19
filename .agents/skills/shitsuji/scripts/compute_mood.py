#!/usr/bin/env python3
"""Compute the unified affective state from HISTORY + persona.

The unified VAD is fused from raw EMA + persona shift only. Long-term
episode recall used to exist as a sidecar, but the minimal runtime keeps no
EPISODES.md file and does not read past memories beyond HISTORY.jsonl.

Inputs:
  - HISTORY.jsonl tail   → raw EMA mood
  - PERSONA_PROFILE.json → halflife + warmth shift + expressive_range
Output JSON (stdout):
  {
    "vad": [v, a, d],                     # unified (raw_ema + persona_shift)
    "vad_components": {                   # transparency / observability
        "raw_ema":       [v, a, d],
        "persona_shift": [dv, da, dd]
    },
    "samples": int,
    "halflife": int,
    "halflife_source": "cli" | "persona_profile" | "default",
    "persona": {...} | null,              # active PERSONA_PROFILE snapshot
    "dyad_cluster": {                     # analytics; NOT a tone driver
        "dyad": str, "count": int, "share": float
    } | null,
    "tendency_candidates": [str, ...],    # Frijda primitives matching unified VAD
    "user_affect": {                      # user baseline + prediction-error hint
        "baseline": {...},
        "runtime_hint": {...}
    },
    "advisory": str                       # ONE sentence summary for host
  }

CLI:
  --window N       number of recent HISTORY entries to consider (default 20)
  --halflife H     override the EMA halflife (otherwise persona-derived)
  --query STR      accepted for backward CLI compatibility; ignored.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SHITSUJI_DATA_DIR") or (SKILL_DIR / "data"))
HISTORY_FILE = DATA_DIR / "HISTORY.jsonl"
CONFIDENCE_FLOOR = 0.1

sys.path.insert(0, str(SKILL_DIR / "scripts"))
import persona_profile  # noqa: E402
import dyad as dyad_lookup  # noqa: E402
import action_tendency  # noqa: E402
import user_affect  # noqa: E402


# ---------------------------------------------------------------------------
# core math: confidence-weighted EMA
# ---------------------------------------------------------------------------


def read_tail(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[dict] = []
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def ema(
    values: list[float],
    confidences: list[float],
    halflife: int,
    initial: float = 0.0,
) -> float:
    """Confidence-weighted EMA starting at `initial` (default 0.0).

    Each entry's effective alpha = base_alpha * max(confidence, CONFIDENCE_FLOOR).
    Halves over `halflife` samples when confidence=1.0.
    """
    if not values:
        return initial
    base_alpha = 1.0 - math.exp(math.log(0.5) / max(halflife, 1))
    acc = initial
    for v, c in zip(values, confidences):
        alpha = min(1.0, base_alpha * max(float(c), CONFIDENCE_FLOOR))
        acc = alpha * v + (1.0 - alpha) * acc
    return acc


# ---------------------------------------------------------------------------
# fusion: persona shift only (episode lookup is a separate hint, not fused)
# ---------------------------------------------------------------------------


def persona_vad_shift(profile: dict | None) -> tuple[float, float, float]:
    """Translate persona traits into a VAD baseline shift.

    `warmth` directly shifts valence (Personality-Affected Emotion
    Generation pattern: persona acts as additive shift, not categorical
    bucket). Other dimensions stay 0 here — persona effects on arousal /
    dominance are mediated by `expressive_range` at the action-tendency
    layer instead, which is closer to where they actually matter.

    Returns (dv, da, dd). All zero when no profile is set.
    """
    if not profile:
        return (0.0, 0.0, 0.0)
    warmth = float(profile.get("warmth", 0.0))
    return (round(warmth * 0.20, 3), 0.0, 0.0)


def dominant_dyad_cluster(entries: list[dict], min_count: int = 2) -> dict | None:
    """Identify the most-common dyad in recent entries.

    Returns analytics-only metadata. The dyad is NOT used to drive tone
    in the redesigned pipeline — that role moved to action_tendency
    (which consumes context, not just cluster identity).
    """
    dyads = [e.get("dyad") for e in entries if e.get("dyad")]
    if len(dyads) < min_count:
        return None
    counter = Counter(dyads)
    most, count = counter.most_common(1)[0]
    if count < min_count:
        return None
    return {"dyad": most, "count": count, "share": round(count / len(entries), 3)}


# ---------------------------------------------------------------------------
# advisory composition: 1-sentence summary
# ---------------------------------------------------------------------------


def _valence_word(v: float) -> str:
    if v < -0.5:
        return "深く沈んだ"
    if v < -0.1:
        return "やや沈み"
    if v < 0.3:
        return "中立"
    if v < 0.7:
        return "前向き"
    return "高揚した"


def _arousal_word(a: float) -> str:
    if a < -0.2:
        return "落ち着いた"
    if a < 0.5:
        return "安定した"
    return "活発な"


def _dominance_phrase(d: float) -> str:
    if d < -0.3:
        return "受け身寄りの"
    if d < 0.3:
        return "対等な"
    return "主導的な"


def compose_advisory(
    vad: tuple[float, float, float],
    dyad_cluster: dict | None,
    persona: dict | None,
) -> str:
    """Generate ONE sentence describing the unified affective state.

    Replaces the legacy 4-fragment tone_directive string concatenation.
    Format: "<valence> · <arousal> · <dominance> mood<, dyad cluster>
             <, episode hint available>".

    The host reads this alongside the unified VAD vector and the action
    tendency candidates, then composes the actual reply tone. When an
    """
    v, a, d = vad
    parts = [
        f"{_dominance_phrase(d)}{_valence_word(v)}・{_arousal_word(a)} mood"
    ]
    if dyad_cluster:
        parts.append(f"{dyad_cluster['dyad']} cluster (×{dyad_cluster['count']})")
    if persona:
        name = persona.get("name") or "custom"
        parts.append(f"persona={name}")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def compute_state(
    query: str | None = None,
    *,
    window: int = 20,
    halflife: int | None = None,
) -> dict:
    """In-process equivalent of `compute_mood.py --query ...`.

    Returns the same dict the CLI prints as JSON (see module docstring).
    Callable from other Python modules (the UserPromptSubmit hook) to
    skip the ~46 ms Python-subprocess startup tax. The CLI `main()` is
    a thin wrapper around this so both forms stay in lock-step.

    Args mirror the CLI flags:
      query    — accepted for backward compatibility; ignored.
      window   — number of recent HISTORY entries to consider.
      halflife — EMA half-life override; None = persona-derived.
    """
    if halflife is not None:
        halflife_source = "cli"
    else:
        halflife = persona_profile.recommended_halflife()
        halflife_source = (
            "persona_profile"
            if persona_profile.read_profile() is not None
            else "default"
        )

    profile = persona_profile.read_profile()
    persona_block = (
        {
            "name": profile.get("name"),
            "volatility": profile.get("volatility"),
            "warmth": profile.get("warmth"),
            "expressive_range": profile.get("expressive_range"),
        }
        if profile
        else None
    )
    expressive_range = float(profile.get("expressive_range", 0.5)) if profile else 0.5

    # --- raw EMA from history ---
    entries = read_tail(HISTORY_FILE, window)
    if entries:
        valences = [float(e.get("valence", 0.0)) for e in entries]
        arousals = [float(e.get("arousal", 0.0)) for e in entries]
        dominances = [float(e.get("dominance", 0.0)) for e in entries]
        confidences = [float(e.get("confidence", 1.0)) for e in entries]
        raw_ema = (
            round(ema(valences, confidences, halflife), 3),
            round(ema(arousals, confidences, halflife), 3),
            round(ema(dominances, confidences, halflife), 3),
        )
    else:
        raw_ema = (0.0, 0.0, 0.0)

    # --- persona shift ---
    persona_shift = persona_vad_shift(profile)

    # --- fusion: raw EMA + persona shift only (NO episode fusion) ---
    unified = (
        max(-1.0, min(1.0, raw_ema[0] + persona_shift[0])),
        max(-1.0, min(1.0, raw_ema[1] + persona_shift[1])),
        max(-1.0, min(1.0, raw_ema[2] + persona_shift[2])),
    )
    unified = (round(unified[0], 3), round(unified[1], 3), round(unified[2], 3))

    # --- analytics-only signals (don't drive tone) ---
    cluster = dominant_dyad_cluster(entries)

    # --- action tendency candidates (host picks final) ---
    candidates = action_tendency.candidates_for_vad(
        unified[0], unified[1], unified[2], expressive_range=expressive_range
    )

    # --- user-side affective baseline (primary response-adaptation signal) ---
    user_model = user_affect.compute_user_model(window=50)

    # --- advisory sentence ---
    advisory = compose_advisory(
        unified, cluster, persona_block
    )

    return {
        "vad": list(unified),
        "vad_components": {
            "raw_ema": list(raw_ema),
            "persona_shift": list(persona_shift),
        },
        "samples": len(entries),
        "halflife": halflife,
        "halflife_source": halflife_source,
        "persona": persona_block,
        "dyad_cluster": cluster,
        "tendency_candidates": candidates,
        "user_affect": user_model,
        "advisory": advisory,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=20)
    ap.add_argument(
        "--halflife",
        type=int,
        default=None,
        help=(
            "EMA half-life in samples. If omitted, derived from active "
            "PERSONA_PROFILE volatility; falls back to 5 when no profile."
        ),
    )
    ap.add_argument(
        "--query",
        type=str,
        default=None,
        help=(
            "User prompt for episode hint lookup. Without --query, "
            "accepted for backward compatibility and ignored."
        ),
    )
    args = ap.parse_args()

    out = compute_state(query=args.query, window=args.window, halflife=args.halflife)
    json.dump(out, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
