#!/usr/bin/env python3
"""Compact task-affect hint for agent handoff.

This module deliberately avoids claiming the user's emotion as fact. It
turns the current prompt + lightweight VAD analysis into a small, stable
control surface for downstream agents: labels, dynamics, and reply policy.
Labels are task-affect labels (doubt, verification_need, momentum), not
clinical or identity claims.
"""

from __future__ import annotations

import json
from typing import Any


TASK_LABELS = (
    "doubt",
    "confusion",
    "pressure",
    "frustration",
    "relief",
    "momentum",
    "curiosity",
    "fatigue",
    "presence_need",
    "self_blame",
    "verification_need",
    "decision_readiness",
)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


def _add(scores: dict[str, float], label: str, amount: float) -> None:
    scores[label] = _clamp(scores.get(label, 0.0) + amount)


def task_label_scores(prompt: str, current_event: dict[str, Any], analysis: dict[str, Any] | None) -> list[dict]:
    text = prompt.strip()
    scores: dict[str, float] = {}

    if _contains_any(text, ("疑", "本当", "ほんと", "根拠", "妥当", "正しい", "違う", "不明", "uncertain", "doubt")):
        _add(scores, "doubt", 0.30)
    if _contains_any(text, ("わから", "分から", "混乱", "整理", "confus", "unclear")):
        _add(scores, "confusion", 0.26)
    if _contains_any(text, ("確認", "検証", "テスト", "比較", "証拠", "evidence", "verify", "check")):
        _add(scores, "verification_need", 0.34)
    if _contains_any(text, ("不安", "焦", "急", "怖", "pressure", "deadline")):
        _add(scores, "pressure", 0.25)
    if _contains_any(text, ("怒", "むかつ", "納得できない", "frustrat")):
        _add(scores, "frustration", 0.28)
    if _contains_any(text, ("なるほど", "OK", "了解", "いいね", "進め", "実装", "やろう")):
        _add(scores, "momentum", 0.22)
    if _contains_any(text, ("知りたい", "教えて", "調べ", "research", "why", "how")):
        _add(scores, "curiosity", 0.22)
    if _contains_any(text, ("疲", "しんど", "無理", "眠", "動けない", "fatigue")):
        _add(scores, "fatigue", 0.26)
    if _contains_any(text, ("寄り添", "聞いて", "ただ聞", "提案じゃなく", "アドバイスいら", "もう動けない", "やっと終わった")):
        _add(scores, "presence_need", 0.36)
    if _contains_any(text, ("自分が", "自分のせい", "自分だけ", "全部遅", "遅くなる", "足引っ張", "迷惑かけ")):
        _add(scores, "self_blame", 0.34)
    if _contains_any(text, ("決め", "採用", "方針", "どっち", "選", "decision")):
        _add(scores, "decision_readiness", 0.20)

    ue = current_event.get("user_emotion", {}) if isinstance(current_event, dict) else {}
    primary = ue.get("primary")
    if primary == "fear":
        _add(scores, "pressure", 0.18)
        _add(scores, "verification_need", 0.08)
    elif primary == "anger":
        _add(scores, "frustration", 0.20)
        _add(scores, "doubt", 0.08)
    elif primary in {"joy", "trust"}:
        _add(scores, "momentum", 0.12)
        _add(scores, "relief", 0.10)

    if isinstance(analysis, dict):
        surprise = float(analysis.get("surprise", 0.0))
        if surprise >= 1.2:
            _add(scores, "pressure", 0.08)
            _add(scores, "verification_need", 0.08)

    if not scores:
        _add(scores, "curiosity", 0.12)

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:4]
    return [
        {"name": name, "score": round(score, 2)}
        for name, score in ranked
        if name in TASK_LABELS and score >= 0.05
    ]


def _band(value: float, *, low: float, high: float) -> str:
    if value < low:
        return "low"
    if value >= high:
        return "high"
    return "medium"


def dynamics(prompt: str, labels: list[dict], current_event: dict[str, Any], analysis: dict[str, Any] | None) -> dict:
    label_names = {item["name"] for item in labels}
    ue = current_event.get("user_emotion", {}) if isinstance(current_event, dict) else {}
    dominance = float(ue.get("dominance", 0.0))
    arousal = float(ue.get("arousal", 0.0))
    uncertainty_score = 0.0
    if {"doubt", "confusion", "verification_need"} & label_names:
        uncertainty_score += 0.45
    if isinstance(analysis, dict):
        uncertainty_score += min(float(analysis.get("surprise", 0.0)) / 4.0, 0.35)

    label_scores = {
        str(item.get("name")): float(item.get("score", 0.0))
        for item in labels
        if isinstance(item, dict)
    }
    positive_momentum = (
        label_scores.get("momentum", 0.0) >= 0.20
        and float(ue.get("valence", 0.0)) >= 0.20
    )
    if _contains_any(prompt, ("落ち着かない", "ざわ", "ザワ", "そわ", "ソワ")) and _contains_any(prompt, ("大丈夫", "だいじょうぶ")):
        need = "unsettled_after_cognitive_safety"
    elif label_scores.get("presence_need", 0.0) >= 0.30:
        need = "emotional_containment"
    elif label_scores.get("self_blame", 0.0) >= 0.30:
        need = "self_blame_low_efficacy"
    elif positive_momentum:
        need = "next_step"
    elif label_scores.get("frustration", 0.0) >= 0.25:
        need = "validity_then_options"
    elif ("pressure" in label_names or "fatigue" in label_names) and label_scores.get("verification_need", 0.0) < 0.20:
        need = "load_reduction"
    elif "verification_need" in label_names:
        need = "verification"
    elif "decision_readiness" in label_names:
        need = "decision"
    elif "pressure" in label_names or "fatigue" in label_names:
        need = "load_reduction"
    elif "momentum" in label_names:
        need = "next_step"
    else:
        need = "clarification"

    if dominance < -0.2:
        control = "lowering"
    elif dominance > 0.25:
        control = "rising"
    else:
        control = "stable"

    risk = "medium" if arousal > 0.65 and ("pressure" in label_names or "frustration" in label_names) else "low"

    return {
        "uncertainty": _band(uncertainty_score, low=0.25, high=0.65),
        "control": control,
        "risk": risk,
        "epistemic_load": "high" if {"doubt", "confusion", "verification_need"} & label_names else "medium",
        "need": need,
    }


def response_policy(labels: list[dict], dyn: dict, persona: dict | None = None) -> list[str]:
    label_names = {item["name"] for item in labels}
    policy = ["do not state task-affect labels as facts"]

    if "doubt" in label_names:
        policy.append("acknowledge doubt briefly")
    if "confusion" in label_names:
        policy.append("split assumptions")
    if dyn.get("need") == "emotional_containment":
        policy.append("acknowledge without turning it into a task")
        policy.append("use short validating presence")
        policy.append("do not analyze causes unless asked")
        policy.append("do not add next actions")
        return policy
    if dyn.get("need") == "validity_then_options":
        policy.append("acknowledge the concern briefly")
        policy.append("separate cause from impact")
        policy.append("offer two concrete options")
    if dyn.get("need") == "verification" or "verification_need" in label_names:
        policy.append("show evidence or calculation path")
        policy.append("offer verification steps")
    if "pressure" in label_names or dyn.get("control") == "lowering":
        policy.append("reduce options and give one next step")
    elif "momentum" in label_names:
        policy.append("keep momentum with concrete next action")

    if persona and float(persona.get("technical_rigor", 0.5)) >= 0.65:
        policy.append("prefer grounded claims over vibe-only reassurance")

    policy.append("end with one concrete next check")

    deduped: list[str] = []
    for item in policy:
        if item not in deduped:
            deduped.append(item)
    return deduped


def response_mode_affinity(labels: list[dict], dyn: dict, persona: dict | None = None) -> dict[str, float]:
    """Estimate conversational stance affinities without selecting a mode.

    These are background state signals, not a response template. Scores are
    independent affinities in [0, 1], so multiple stances may be plausible at
    once and the host model can still choose naturally from the task context.
    """
    label_scores = {
        str(item.get("name")): float(item.get("score", 0.0))
        for item in labels
        if isinstance(item, dict)
    }
    need = str(dyn.get("need") or "")
    control = str(dyn.get("control") or "")
    uncertainty = str(dyn.get("uncertainty") or "")
    epistemic_load = str(dyn.get("epistemic_load") or "")

    presence = 0.18
    orientation = 0.22
    exploration = 0.14
    execution = 0.12
    verification = 0.12

    presence += label_scores.get("presence_need", 0.0) * 1.35
    presence += label_scores.get("fatigue", 0.0) * 0.70
    presence += label_scores.get("self_blame", 0.0) * 0.45
    if need in {"emotional_containment", "load_reduction"}:
        presence += 0.24
    if control == "lowering":
        presence += 0.10

    orientation += label_scores.get("self_blame", 0.0) * 0.95
    orientation += label_scores.get("confusion", 0.0) * 0.85
    orientation += label_scores.get("pressure", 0.0) * 0.45
    orientation += label_scores.get("doubt", 0.0) * 0.35
    if need in {"self_blame_low_efficacy", "load_reduction", "validity_then_options"}:
        orientation += 0.24

    exploration += label_scores.get("curiosity", 0.0) * 1.10
    exploration += label_scores.get("doubt", 0.0) * 0.25
    if uncertainty == "medium":
        exploration += 0.12
    if need == "clarification":
        exploration += 0.10

    execution += label_scores.get("momentum", 0.0) * 1.20
    execution += label_scores.get("decision_readiness", 0.0) * 0.80
    if need in {"next_step", "decision"}:
        execution += 0.24
    if control == "lowering" or need in {"emotional_containment", "self_blame_low_efficacy"}:
        execution -= 0.08

    verification += label_scores.get("verification_need", 0.0) * 1.30
    verification += label_scores.get("doubt", 0.0) * 0.75
    if need == "verification":
        verification += 0.28
    if epistemic_load == "high":
        verification += 0.10

    if persona and float(persona.get("technical_rigor", 0.5)) >= 0.65:
        verification += 0.05
        orientation += 0.03

    return {
        "presence": round(_clamp(presence), 2),
        "orientation": round(_clamp(orientation), 2),
        "exploration": round(_clamp(exploration), 2),
        "execution": round(_clamp(execution), 2),
        "verification": round(_clamp(verification), 2),
    }


def assistant_delta(labels: list[dict], dyn: dict) -> dict[str, float]:
    """Return compact calibration against generic assistant defaults."""
    need = str(dyn.get("need") or "")
    table = {
        "unsettled_after_cognitive_safety": {
            "solve": -0.70,
            "ask": -0.55,
            "load": -0.45,
            "takeover": -0.80,
        },
        "emotional_containment": {
            "solve": -0.98,
            "ask": -0.95,
            "load": -0.90,
            "takeover": -0.95,
        },
        "self_blame_low_efficacy": {
            "solve": -0.75,
            "ask": -0.55,
            "load": -0.40,
            "takeover": -0.85,
        },
        "load_reduction": {
            "solve": -0.70,
            "ask": -0.45,
            "load": -0.60,
            "takeover": -0.80,
        },
        "verification": {
            "solve": 0.20,
            "ask": -0.05,
            "load": 0.05,
            "takeover": -0.40,
        },
        "validity_then_options": {
            "solve": -0.35,
            "ask": -0.20,
            "load": -0.25,
            "takeover": -0.65,
        },
        "decision": {
            "solve": 0.30,
            "ask": -0.10,
            "load": -0.05,
            "takeover": -0.35,
        },
        "next_step": {
            "solve": 0.45,
            "ask": 0.00,
            "load": 0.00,
            "takeover": -0.25,
        },
        "clarification": {
            "solve": -0.35,
            "ask": -0.20,
            "load": -0.20,
            "takeover": -0.65,
        },
    }
    return table.get(need, table["clarification"])


def state_insight(dyn: dict) -> str:
    need = str(dyn.get("need") or "")
    table = {
        "unsettled_after_cognitive_safety": "lower pressure and leave the unease tolerable; do not make resolving it the task.",
        "emotional_containment": "the user is reporting depleted capacity, not requesting planning; reduce interaction demand.",
        "self_blame_low_efficacy": "restore interpretive space; do not turn it into an improvement plan.",
        "load_reduction": "reduce processing load and preserve optionality; do not expand the problem space.",
        "verification": "make uncertainty inspectable without turning verification into a burden.",
        "validity_then_options": "separate validity from next moves; do not rush the user into action.",
        "decision": "support judgment while keeping the user's agency primary.",
        "next_step": "support momentum without taking over ownership.",
        "clarification": "keep the response light; do not over-infer the user's intent.",
    }
    return table.get(need, table["clarification"])


def compact_state_name(dyn: dict) -> str:
    need = str(dyn.get("need") or "")
    names = {
        "emotional_containment": "post_completion_depletion",
        "self_blame_low_efficacy": "self_efficacy_compression",
    }
    return names.get(need, need or "unknown")


def response_state(labels: list[dict], dyn: dict, persona: dict | None = None) -> dict:
    """Return low-level state signals for the host agent.

    The capsule should describe the user's likely affective dynamics, not
    prescribe the final response text or a deterministic micro-policy.
    """
    need = str(dyn.get("need") or "calibrated_response")
    state_table = {
        "emotional_containment": {
            "intervention_level": "hold",
            "initiative_level": "passive",
            "expressiveness": "quiet",
            "relational_pressure": "minimal",
            "solution_pressure": "none",
        },
        "self_blame_low_efficacy": {
            "intervention_level": "orient",
            "initiative_level": "responsive",
            "expressiveness": "warm",
            "relational_pressure": "low",
            "solution_pressure": "none",
        },
        "verification": {
            "intervention_level": "assist",
            "initiative_level": "responsive",
            "expressiveness": "focused",
            "relational_pressure": "low",
            "solution_pressure": "medium",
        },
        "load_reduction": {
            "intervention_level": "orient",
            "initiative_level": "responsive",
            "expressiveness": "quiet",
            "relational_pressure": "minimal",
            "solution_pressure": "none",
        },
        "validity_then_options": {
            "intervention_level": "orient",
            "initiative_level": "responsive",
            "expressiveness": "warm",
            "relational_pressure": "medium",
            "solution_pressure": "low",
        },
        "decision": {
            "intervention_level": "assist",
            "initiative_level": "responsive",
            "expressiveness": "focused",
            "relational_pressure": "low",
            "solution_pressure": "medium",
        },
        "next_step": {
            "intervention_level": "act",
            "initiative_level": "proactive",
            "expressiveness": "energetic",
            "relational_pressure": "low",
            "solution_pressure": "high",
        },
        "clarification": {
            "intervention_level": "orient",
            "initiative_level": "responsive",
            "expressiveness": "warm",
            "relational_pressure": "low",
            "solution_pressure": "none",
        },
    }
    selected = state_table.get(need, state_table["clarification"])
    return {
        "affective_state": need,
        "latent_dynamics": {
            "uncertainty": dyn.get("uncertainty", "unknown"),
            "control": dyn.get("control", "unknown"),
            "risk": dyn.get("risk", "unknown"),
            "epistemic_load": dyn.get("epistemic_load", "unknown"),
        },
        "intervention_level": selected["intervention_level"],
        "initiative_level": selected["initiative_level"],
        "expressiveness": selected["expressiveness"],
        "relational_pressure": selected["relational_pressure"],
        "solution_pressure": selected["solution_pressure"],
        "response_mode_affinity": response_mode_affinity(labels, dyn, persona),
        "compact_state": compact_state_name(dyn),
        "assistant_delta": assistant_delta(labels, dyn),
        "state_insight": state_insight(dyn),
    }


def build_affect_hint(
    *,
    prompt: str,
    current_event: dict[str, Any],
    current_analysis: dict[str, Any] | None,
    persona: dict | None,
    source: str = "heuristic_readonly",
) -> dict:
    labels = task_label_scores(prompt, current_event, current_analysis)
    dyn = dynamics(prompt, labels, current_event, current_analysis)
    confidence = float(current_event.get("confidence", 0.0)) if isinstance(current_event, dict) else 0.0
    return {
        "affect_hint_version": 1,
        "source": source,
        "confidence": round(_clamp(confidence), 2),
        "labels": labels,
        "dynamics": dyn,
        "response_state": response_state(labels, dyn, persona),
        "response_policy": response_policy(labels, dyn, persona),
    }


def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    args = parser.parse_args()
    hint = build_affect_hint(
        prompt=args.prompt,
        current_event={"confidence": 0.32, "user_emotion": {"primary": "neutral", "dominance": 0.0, "arousal": 0.0}},
        current_analysis=None,
        persona=None,
    )
    json.dump(hint, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
