#!/usr/bin/env python3
"""UserPromptSubmit hook for shitsuji Skill.

Pipeline (deterministic order, all sub-second):

    1. read hook payload (JSON) from stdin
    2. extract user prompt + transcript_path
    3. compute the user affect dynamics prior from HISTORY.jsonl
    4. write a lightweight auto-memory event for every user prompt
    5. emit short response guidance as additionalContext

Self-harm / self-care framing is intentionally NOT handled here — Codex's
base safety training already produces appropriate responses to those
prompts. The skill is scoped to "personal emotion log + tone calibration"
and does not implement clinical safety.

Hook contract: Codex UserPromptSubmit
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "shitsuji"
SCRIPTS_DIR = SKILL_DIR / "scripts"
PROMPTS_DIR = SKILL_DIR / "prompts"
DATA_DIR = Path(os.environ.get("SHITSUJI_DATA_DIR") or (SKILL_DIR / "data"))
HISTORY_FILE = DATA_DIR / "HISTORY.jsonl"
LOG_FILE = DATA_DIR / "hook.log"
RUBRIC_FILE = PROMPTS_DIR / "vad-rubric.md"
APPEND_SCRIPT = SCRIPTS_DIR / "append.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import persona_profile  # noqa: E402  — persona metadata only
import user_affect  # noqa: E402  — user affect dynamics / response_control
import affect_hint  # noqa: E402  — compact task-affect handoff hint


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def is_internal_codex_prompt(prompt: str) -> bool:
    """Detect Codex housekeeping prompts that should not enter memory.

    UserPromptSubmit can fire for non-user model tasks such as automatic
    thread-title generation. Those prompts are implementation detail, not
    conversation memory, and injecting shitsuji context there is pure context
    pollution.
    """
    text = " ".join(prompt.strip().split())
    internal_markers = (
        "provide a short title for a task that will be created from that prompt",
        "The tasks typically involve",
        "You will be presented with a user prompt",
    )
    return any(marker in text for marker in internal_markers)


# ---------------------------------------------------------------------------
# optional logging
# ---------------------------------------------------------------------------


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
            f.write(f"[{ts}] user_prompt_submit: {msg}\n")
    except OSError:
        pass


def log_json(event: str, payload: dict) -> None:
    if os.environ.get("SHITSUJI_HOOK_LOG") != "1":
        return
    log(f"{event} {json.dumps(payload, ensure_ascii=False, sort_keys=True)}")


def is_first_user_prompt(transcript_path: Path | None) -> bool:
    """True iff no prior real user prompt exists in this session's transcript."""
    if transcript_path is None or not transcript_path.exists():
        return True
    try:
        with transcript_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                if ev.get("type") != "user" or ev.get("isMeta"):
                    continue
                msg = ev.get("message", {})
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    for block in content:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "text"
                        ):
                            text = block.get("text", "")
                            break
                if not text.strip() or text.lstrip().startswith("<command-name>"):
                    continue
                return False
    except OSError as e:
        log(f"transcript read failed: {e}")
        return True
    return True


# ---------------------------------------------------------------------------
# lightweight project memory
# ---------------------------------------------------------------------------


def estimate_user_turn(prompt: str) -> dict:
    """Return a schema-valid, low-confidence affect event for every prompt.

    This is intentionally modest: it gives the project-local dynamics prior a
    continuous observation stream without pretending to be a full LLM emotion
    classifier. The host can still reason more precisely from the raw turn.
    """
    text = prompt.strip()
    lowered = text.lower()
    v = 0.0
    a = 0.0
    d = 0.0
    primary = "neutral"
    secondary: str | None = None
    confidence = 0.32
    reason = "lightweight auto-memory: no explicit affect cue; storing neutral project turn"

    if _contains_any(text, ("おはよう", "こんにちは", "こんばんは", "やほ", "ヤッホ", "hello", "hi")):
        v, a, d = 0.28, 0.12, 0.12
        primary, secondary = "trust", "joy"
        confidence = 0.50
        reason = "lightweight auto-memory: greeting / affiliative cue"
    if _contains_any(text, ("ありがとう", "助かった", "うれしい", "嬉しい", "最高", "いいね", "やった")):
        v, a, d = max(v, 0.55), max(a, 0.28), max(d, 0.25)
        primary, secondary = "joy", "trust"
        confidence = max(confidence, 0.58)
        reason = "lightweight auto-memory: gratitude or positive affect cue"
    if _contains_any(text, ("不安", "緊張", "焦", "怖", "こわ", "寝れない", "心配")):
        v, a, d = min(v, -0.45), max(a, 0.55), min(d, -0.35)
        primary, secondary = "fear", "anticipation"
        confidence = max(confidence, 0.60)
        reason = "lightweight auto-memory: anxiety / tension cue"
    if _contains_any(text, ("最悪", "つらい", "辛い", "しんど", "心折れ", "疲れ", "無理", "虚しい")):
        v, d = min(v, -0.55), min(d, -0.35)
        a = a if a >= 0.35 else min(a, -0.10)
        primary, secondary = "sadness", None
        confidence = max(confidence, 0.62)
        reason = "lightweight auto-memory: distress / low-valence cue"
    if _contains_any(text, ("むかつ", "ムカつ", "ふざけ", "怒", "腹立", "納得できない")):
        v, a, d = min(v, -0.55), max(a, 0.55), max(d, 0.25)
        primary, secondary = "anger", None
        confidence = max(confidence, 0.62)
        reason = "lightweight auto-memory: anger / frustration cue"
    if _contains_any(text, ("本当に", "ほんとに", "正しい", "合って", "あって", "確認", "検証", "根拠")):
        v, a, d = min(v, -0.20), max(a, 0.32), min(d, -0.25)
        primary, secondary = "surprise", None
        confidence = max(confidence, 0.54)
        reason = "lightweight auto-memory: doubt / verification cue"

    if "!" in text or "！" in text:
        a += 0.10
    if "..." in text or "…" in text:
        a -= 0.06
    if "?" in text or "？" in text:
        d -= 0.03
    if any(token in lowered for token in ("todo", "fix", "bug", "git", "python", "typescript", "api")):
        confidence = min(confidence, 0.45)

    user_emotion = {
        "valence": round(_clamp(v, -1.0, 1.0), 3),
        "arousal": round(_clamp(a, -1.0, 1.0), 3),
        "dominance": round(_clamp(d, -1.0, 1.0), 3),
        "primary": primary,
        "rationale": f"{reason}; text={text[:180]!r}",
    }
    intensity = max(abs(user_emotion["valence"]), abs(user_emotion["arousal"]), abs(user_emotion["dominance"]))
    return {
        **user_emotion,
        "secondary": secondary,
        "intensity": round(_clamp(intensity, 0.05, 1.0), 3),
        "confidence": round(confidence, 3),
        "rationale": "project-level lightweight auto-memory; top-level mirrors user_emotion (AI mood EMA removed)",
        "user_emotion": user_emotion,
        "source": "user_prompt_submit_auto_memory",
    }


def append_auto_memory(event: dict) -> bool:
    if os.environ.get("SHITSUJI_AUTO_APPEND", "1") == "0":
        return False
    env = os.environ.copy()
    env["SHITSUJI_DATA_DIR"] = str(DATA_DIR)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [sys.executable, str(APPEND_SCRIPT)],
            input=json.dumps(event, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=3,
            env=env,
        )
    except Exception as e:  # noqa: BLE001
        log(f"auto-memory append failed: {e!r}")
        return False
    if proc.returncode != 0:
        log(f"auto-memory append rejected: {proc.stderr.strip()}")
        return False
    log("auto-memory appended")
    return True


# ---------------------------------------------------------------------------
# user affect invocation
# ---------------------------------------------------------------------------


_USER_AFFECT_FALLBACK = {
    "user_affect": {
        "runtime_hint": {
            "affect_prior_mean": {"valence": 0.0, "arousal": 0.0, "dominance": 0.0},
            "affect_prior_cov_diag": {"valence": 0.20, "arousal": 0.20, "dominance": 0.20},
            "filtered_state": {"valence": 0.0, "arousal": 0.0, "dominance": 0.0},
            "samples": 0,
            "reliability": 0.0,
            "free_energy_model": "diagonal_vfe_proxy",
            "host_task": "Score current user_emotion, compute VFE, derive response_control.",
        }
    },
    "persona": None,
}


def read_persona_block() -> dict | None:
    try:
        profile = persona_profile.read_profile()
    except Exception as e:  # noqa: BLE001
        log(f"persona read failed: {e!r}")
        return None
    if not profile:
        return None
    return {
        "name": profile.get("name"),
        "warmth": profile.get("warmth"),
        "expressive_range": profile.get("expressive_range"),
        "technical_rigor": profile.get("technical_rigor", 0.5),
    }


def run_user_context(query: str | None) -> dict:
    """Compute hot-path user adaptation context.

    AI mood EMA is intentionally not part of this hook. This skill is a
    lightweight response-policy adapter, so the hot path only injects the
    user affect dynamics prior, heuristic sidecar, and persona metadata.
    """
    try:
        model = user_affect.compute_user_model(window=50)
    except Exception as e:  # noqa: BLE001 — fail-safe fallback for hook layer
        log(f"user_affect failed: {e!r}")
        model = _USER_AFFECT_FALLBACK["user_affect"]
    current_event = estimate_user_turn(query or "")
    current_analysis = None
    try:
        prior = model.get("affect_prior") or {}
        if not prior:
            hint = model.get("runtime_hint", {})
            prior = {
                "predicted_user_vad": hint.get("affect_prior_mean", {}),
                "predicted_cov_diag": hint.get("affect_prior_cov_diag", {}),
                "samples": hint.get("samples", 0),
                "reliability": hint.get("reliability", 0.0),
                "dynamics": {"filtered_state": hint.get("filtered_state", {})},
            }
        base_analysis = user_affect.analyze_affective_free_energy(
            current_event["user_emotion"],
            prior,
            confidence=float(current_event.get("confidence", 0.35)),
        )
        base_analysis["state_mode"] = "heuristic_readonly"
    except Exception as e:  # noqa: BLE001
        log(f"current affect analysis failed: {e!r}")
        base_analysis = None
    persona = read_persona_block()
    hint = affect_hint.build_affect_hint(
        prompt=query or "",
        current_event=current_event,
        current_analysis=base_analysis,
        persona=persona,
        source="heuristic_readonly",
    )
    current_analysis = base_analysis
    try:
        if base_analysis is not None:
            current_analysis = user_affect.analyze_affective_free_energy(
                current_event["user_emotion"],
                prior,
                confidence=float(current_event.get("confidence", 0.35)),
                interaction=hint,
            )
            current_analysis["state_mode"] = "heuristic_readonly"
            hint = affect_hint.build_affect_hint(
                prompt=query or "",
                current_event=current_event,
                current_analysis=current_analysis,
                persona=persona,
                source="heuristic_readonly",
            )
    except Exception as e:  # noqa: BLE001
        log(f"interaction-gated affect analysis failed: {e!r}")
    return {
        "user_affect": model,
        "current_estimate": current_event,
        "current_analysis": current_analysis,
        "persona": persona,
        "affect_hint": hint,
    }


# ---------------------------------------------------------------------------
# rendering: short response guidance
# ---------------------------------------------------------------------------


def _persona_label(persona: dict | None) -> str:
    if not persona:
        return "none"
    return (
        f"{persona.get('name') or 'derived'} "
        f"(warm={float(persona.get('warmth', 0.0)):+.2f}, "
        f"range={float(persona.get('expressive_range', 0.0)):.2f}, "
        f"rigor={float(persona.get('technical_rigor', 0.5)):.2f})"
    )


def _affect_prior_summary(user_affect: dict | None) -> str:
    if not isinstance(user_affect, dict):
        return "unavailable"
    hint = user_affect.get("runtime_hint")
    if not isinstance(hint, dict):
        return "unavailable"
    mean = hint.get("affect_prior_mean", {})
    samples = int(hint.get("samples", 0))
    reliability = float(hint.get("reliability", 0.0))
    valence = float(mean.get("valence", 0.0))
    arousal = float(mean.get("arousal", 0.0))
    if samples == 0 or reliability < 0.2:
        trend = "insufficient-history"
    elif arousal >= 0.35 and valence < -0.15:
        trend = "tense"
    elif valence >= 0.25:
        trend = "positive"
    elif valence <= -0.25:
        trend = "low-valence"
    else:
        trend = "steady"
    return (
        f"n={samples} rel={reliability:.2f} trend={trend} "
        f"prior_vad=({valence:+.2f},{arousal:+.2f},{float(mean.get('dominance', 0.0)):+.2f})"
    )


def _fmt_scores(values: dict, keys: tuple[str, ...]) -> str:
    return " ".join(f"{key}={float(values.get(key, 0.0)):.2f}" for key in keys)


def _level(value: float, medium: float, high: float) -> str:
    if value >= high:
        return "high"
    if value >= medium:
        return "medium"
    return "low"


def render_policy_capsule(snapshot: dict, *, memory_updated: bool) -> str:
    """Render compact response guidance for the host agent."""
    analysis = snapshot.get("current_analysis") if isinstance(snapshot, dict) else {}
    if not isinstance(analysis, dict):
        analysis = {}
    vfe = analysis.get("variational_free_energy") if isinstance(analysis.get("variational_free_energy"), dict) else {}
    modes = analysis.get("regulation_mode") if isinstance(analysis.get("regulation_mode"), dict) else {}
    needs = analysis.get("regulation_needs") if isinstance(analysis.get("regulation_needs"), dict) else {}
    control = analysis.get("response_control") if isinstance(analysis.get("response_control"), dict) else {}
    surface = analysis.get("response_surface") if isinstance(analysis.get("response_surface"), dict) else {}
    target = str(vfe.get("regulation_target") or "maintain_continuity")
    effective_uncertainty = float(needs.get("effective_uncertainty", needs.get("reduce_uncertainty", 0.0)))
    analysis_text = str(analysis.get("analysis_summary") or "user input appears stable enough for a normal calibrated response.")
    insight = "derive response shape from VFE; do not state affect hypotheses as facts."
    if "verification or evidence" in analysis_text:
        insight = "answer from grounded evidence first; inspect files or concrete context when available before reassurance."
    elif "not necessarily task-verification" in analysis_text:
        insight = "keep the reply compact; acknowledge the uncertainty without sorting it into categories or a plan."
    elif effective_uncertainty < 0.40 and float(modes.get("agency", 0.0)) >= 0.55:
        insight = "restore agency without turning the uncertainty into a decomposition task."
    elif effective_uncertainty < 0.40 and float(modes.get("containment", 0.0)) >= 0.30:
        insight = "hold uncertainty lightly; avoid dense questions, options, or explanation."
    elif target == "reduce_uncertainty":
        insight = "reduce uncertainty with structure while preserving the user's exploratory thread."
    elif target == "dampen_affective_velocity":
        insight = "lower affective velocity before adding options or dense explanation."
    elif target == "reduce_control_load":
        insight = "restore agency and limit cognitive load; avoid taking over."
    log_json(
        "affect_debug",
        {
            "vfe": vfe,
            "regulation_mode": modes,
            "regulation_needs": needs,
            "response_control": control,
            "response_surface": surface,
            "analysis_summary": analysis_text,
            "insight": insight,
        },
    )
    return "\n".join(
        [
            "## shitsuji response guidance",
            f"- analysis_summary: {analysis_text}",
            (
                "- response_surface: "
                f"length={surface.get('length', 'medium')} "
                f"followup={surface.get('followup', 'medium')} "
                f"structure={surface.get('structure', 'medium')} "
                f"action={surface.get('action', 'user_led')}"
            ),
            f"- insight: {insight}",
        ]
    )


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------


def emit(additional_context: str | None) -> None:
    payload = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit"}}
    if additional_context:
        payload["hookSpecificOutput"]["additionalContext"] = additional_context
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    raw = sys.stdin.read()
    try:
        hook_input = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        log("hook stdin was not JSON; emitting no-op")
        emit(None)
        return

    prompt = hook_input.get("prompt") or ""
    if not prompt.strip():
        emit(None)
        return
    if is_internal_codex_prompt(prompt):
        log("ignored internal Codex housekeeping prompt")
        emit(None)
        return

    if os.environ.get("SHITSUJI_DISABLED") == "1":
        log("disabled via SHITSUJI_DISABLED=1")
        emit(None)
        return

    if not RUBRIC_FILE.exists():
        log(f"rubric file missing at {RUBRIC_FILE}")
        emit(None)
        return

    transcript_raw = hook_input.get("transcript_path")
    transcript_path = Path(transcript_raw) if isinstance(transcript_raw, str) else None

    # Keep the transcript read for behavior compatibility and future policy
    # hooks, but do not persist a separate cache/flag file for injection mode.
    is_first_user_prompt(transcript_path)

    snapshot = run_user_context(prompt)
    memory_updated = append_auto_memory(snapshot.get("current_estimate", {}))
    log("response guidance emitted")
    emit(render_policy_capsule(snapshot, memory_updated=memory_updated))


if __name__ == "__main__":
    main()
