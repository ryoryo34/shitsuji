#!/usr/bin/env python3
"""Render or regenerate a profile snapshot from the current HISTORY.jsonl tail.

Produces a human-readable markdown snapshot of the shitsuji's recent mood
state. Purely deterministic: identical HISTORY.jsonl tails yield identical
profile text.

The SessionStart hook uses this in memory so normal runtime only persists
HISTORY.jsonl and PERSONA_PROFILE.json. The CLI still writes PROFILE.md as an
explicit debugging/export command.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from collections import Counter
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SHITSUJI_DATA_DIR") or (SKILL_DIR / "data"))
HISTORY_FILE = DATA_DIR / "HISTORY.jsonl"
PROFILE_FILE = DATA_DIR / "PROFILE.md"

WINDOW = 20
LONG_WINDOW = 100

sys.path.insert(0, str(SKILL_DIR / "scripts"))
import compute_mood as _compute_mood  # noqa: E402  — sibling module after sys.path insert
import persona_profile  # noqa: E402
import affect_hint  # noqa: E402


def read_tail(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-n:]:
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


def render_profile(short: dict, long: dict, recent: list[dict]) -> str:
    """Render PROFILE.md.

    The format is fixed so that diffs over time are easy to read. Phase
    9f schema: mood["vad"] is the unified VAD list, advisory replaces
    summary/tone_directive, dyad_cluster replaces "primary mode".
    """
    now = (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    def fmt_vad(d: dict) -> str:
        v = d.get("vad", [0.0, 0.0, 0.0])
        return f"v={v[0]:+.2f} a={v[1]:+.2f} d={v[2]:+.2f}"

    def fmt_cluster(d: dict) -> str:
        c = d.get("dyad_cluster")
        if not c:
            return "(no dominant dyad)"
        return f"{c['dyad']} (×{c['count']}, share={c['share']:.2f})"

    primary_counts = Counter(
        str(e.get("primary", "neutral")) for e in recent
    ).most_common(5)
    primary_block = (
        ", ".join(f"{lbl}×{cnt}" for lbl, cnt in primary_counts)
        if primary_counts
        else "(no entries)"
    )

    last_entry = recent[-1] if recent else None
    persona = persona_profile.read_profile()
    style = persona.get("style_profile", {}) if persona else {}
    style_lines = ""
    if isinstance(style, dict) and style:
        ordered_keys = ("tone", "distance", "formality", "playfulness", "explanation", "praise", "challenge")
        lines = [
            f"- {key}: {style[key]}"
            for key in ordered_keys
            if style.get(key)
        ]
        boundaries = style.get("boundaries")
        if isinstance(boundaries, list) and boundaries:
            lines.append("- boundaries: " + "; ".join(str(item) for item in boundaries[:3]))
        style_lines = "\n".join(lines)
    persona_block = (
        f"- source: {persona.get('source_kind', 'unknown')}\n"
        f"- name: {persona.get('name', '(unnamed)')}\n"
        f"- volatility: {float(persona.get('volatility', 0.5)):.2f}\n"
        f"- warmth: {float(persona.get('warmth', 0.0)):+.2f}\n"
        f"- expressive_range: {float(persona.get('expressive_range', 0.5)):.2f}\n"
        f"- technical_rigor: {float(persona.get('technical_rigor', 0.5)):.2f}"
        + (f"\n\n### Structured style\n\n{style_lines}" if style_lines else "")
        if persona
        else "(no persona profile derived)"
    )

    if last_entry:
        latest_hint = affect_hint.build_affect_hint(
            prompt=str(last_entry.get("user_emotion", {}).get("rationale", "")),
            current_event=last_entry,
            current_analysis=None,
            persona=persona,
            source="history_latest_heuristic_readonly",
        )
        hint_block = (
            f"- need: {latest_hint['dynamics']['need']}\n"
            f"- uncertainty: {latest_hint['dynamics']['uncertainty']}\n"
            f"- labels: "
            + ", ".join(f"{item['name']}={item['score']:.2f}" for item in latest_hint["labels"])
            + "\n"
            f"- policy: {'; '.join(latest_hint['response_policy'][:3])}"
        )
    else:
        hint_block = "(no recent hint)"

    last_block = (
        f"- ts: {last_entry.get('ts', '?')}\n"
        f"- primary: {last_entry.get('primary', '?')}"
        f"{('+' + last_entry['secondary']) if last_entry.get('secondary') else ''}"
        f"{(' = ' + last_entry['dyad']) if last_entry.get('dyad') else ''}\n"
        f"- VAD: v={float(last_entry.get('valence', 0.0)):+.2f} "
        f"a={float(last_entry.get('arousal', 0.0)):+.2f} "
        f"d={float(last_entry.get('dominance', 0.0)):+.2f}\n"
        f"- rationale: {last_entry.get('rationale', '')}"
        if last_entry
        else "(no entries yet)"
    )

    return f"""# shitsuji PROFILE

_Last updated: {now} (auto-generated, do not edit by hand)_

## Short-term mood (last {short['samples']} entries, halflife {short['halflife']})

- VAD: {fmt_vad(short)}
- advisory: {short.get('advisory', '(none)')}
- dyad cluster: {fmt_cluster(short)}
- tendency candidates: {', '.join(short.get('tendency_candidates', [])) or '(none)'}

## Long-term mood (last {long['samples']} entries, halflife {long['halflife']})

- VAD: {fmt_vad(long)}
- advisory: {long.get('advisory', '(none)')}
- dyad cluster: {fmt_cluster(long)}

## Primary-emotion frequencies (recent window)

{primary_block}

## Persona

{persona_block}

## Latest affect hint

{hint_block}

## Last input

{last_block}
"""


def update() -> Path:
    """Regenerate PROFILE.md for explicit CLI/debug use; return the path."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_FILE.write_text(render_current_profile(), encoding="utf-8")
    return PROFILE_FILE


def render_current_profile() -> str:
    """Return the current profile text without writing any runtime file."""
    short = _compute_mood.compute_state(window=WINDOW, halflife=5)
    long_term = _compute_mood.compute_state(window=LONG_WINDOW, halflife=20)
    recent = read_tail(HISTORY_FILE, WINDOW)
    return render_profile(short, long_term, recent)


def main() -> None:
    print(str(update()))


if __name__ == "__main__":
    main()
