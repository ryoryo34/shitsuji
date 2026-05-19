#!/usr/bin/env python3
"""SessionStart hook for shitsuji Skill.

Pipeline:
    1. create/refresh PERSONA_PROFILE.json if instruction sources changed
    2. compute a compact baseline summary from HISTORY.jsonl
    3. emit a short additionalContext capsule for the session.

Failure policy: fail-safe. Errors log to data/hook.log only when
SHITSUJI_HOOK_LOG=1; emitted output is a no-op when state is unavailable.

Hook contract:
    Codex SessionStart
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "shitsuji"
SCRIPTS_DIR = SKILL_DIR / "scripts"
DATA_DIR = Path(os.environ.get("SHITSUJI_DATA_DIR") or (SKILL_DIR / "data"))
LOG_FILE = DATA_DIR / "hook.log"

sys.path.insert(0, str(SCRIPTS_DIR))
import persona_profile  # noqa: E402  — sibling module after sys.path insert
import compute_mood  # noqa: E402  — inlined to skip a Python subprocess


def log(msg: str) -> None:
    if os.environ.get("SHITSUJI_HOOK_LOG") != "1":
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] session_start: {msg}\n")
    except OSError:
        pass


def emit(additional_context: str | None) -> None:
    payload = {"hookSpecificOutput": {"hookEventName": "SessionStart"}}
    if additional_context:
        payload["hookSpecificOutput"]["additionalContext"] = additional_context
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def _persona_label(profile: dict | None) -> str:
    if not profile:
        return "default"
    return (
        f"{profile.get('name') or 'derived'} "
        f"(warm={float(profile.get('warmth', 0.0)):+.2f}, "
        f"range={float(profile.get('expressive_range', 0.5)):.2f}, "
        f"rigor={float(profile.get('technical_rigor', 0.5)):.2f})"
    )


def render_session_start_context(profile: dict | None) -> str:
    """Return a concise state capsule; no rubrics or raw history."""
    try:
        mood = compute_mood.compute_state()
    except Exception as e:  # noqa: BLE001 — fail-safe: degrade to neutral display
        log(f"compute_mood inline failed: {e!r}")
        mood = None

    if mood:
        advisory_line = mood.get("advisory", "(no advisory)")
        vad = mood.get("vad", [0.0, 0.0, 0.0])
        vad_line = f"v={vad[0]:+.3f} a={vad[1]:+.3f} d={vad[2]:+.3f}"
        samples = int(mood.get("samples", 0))
        halflife = int(mood.get("halflife", 5))
    else:
        advisory_line = "(unavailable)"
        vad_line = "(unavailable)"
        samples = 0
        halflife = 5

    return "\n".join(
        [
            "## shitsuji session capsule",
            f"- baseline: n={samples} halflife={halflife} vad=({vad_line})",
            f"- advisory: {advisory_line}",
            f"- persona: {_persona_label(profile)}",
            "- policy: UserPromptSubmit will append turns and inject short response-policy capsules.",
            "- privacy: no raw history, prompt text, derivation rubric, or VAD rubric is injected here.",
        ]
    )


def main() -> None:
    # Drain stdin even though we don't use the payload (hooks always send one).
    try:
        sys.stdin.read()
    except OSError:
        pass

    try:
        profile = persona_profile.ensure_profile_current()
    except Exception as e:  # noqa: BLE001 — fail-safe: skip injection on error
        log(f"persona profile ensure failed: {e!r}")
        emit(None)
        return

    additional_context = render_session_start_context(profile)
    emit(additional_context)


if __name__ == "__main__":
    main()
