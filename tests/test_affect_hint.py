from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AFFECT_HINT_MODULE = (
    REPO_ROOT / ".agents" / "skills" / "shitsuji" / "scripts" / "affect_hint.py"
)


def _load_module():
    sys.modules.pop("affect_hint_under_test", None)
    spec = importlib.util.spec_from_file_location("affect_hint_under_test", AFFECT_HINT_MODULE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["affect_hint_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_doubt_and_verification_prompt_gets_agent_safe_policy() -> None:
    mod = _load_module()
    hint = mod.build_affect_hint(
        prompt="この設計で本当にいいのか少し疑ってる。根拠を確認したい",
        current_event={
            "confidence": 0.32,
            "user_emotion": {"primary": "neutral", "arousal": 0.0, "dominance": 0.0},
        },
        current_analysis=None,
        persona={"technical_rigor": 0.8},
    )
    labels = {item["name"] for item in hint["labels"]}
    assert {"doubt", "verification_need"} <= labels
    assert hint["dynamics"]["need"] == "verification"
    assert hint["response_state"]["affective_state"] == "verification"
    assert hint["response_state"]["intervention_level"] == "assist"
    assert hint["response_state"]["initiative_level"] == "responsive"
    assert hint["response_state"]["expressiveness"] == "focused"
    assert hint["response_state"]["relational_pressure"] == "low"
    assert hint["response_state"]["solution_pressure"] == "medium"
    assert hint["response_state"]["assistant_delta"]["solve"] > 0
    assert hint["response_state"]["assistant_delta"]["takeover"] < 0
    assert "uncertainty inspectable" in hint["response_state"]["state_insight"]
    assert "response_guidance" not in hint["response_state"]
    assert "action_budget" not in hint["response_state"]


def test_decision_readiness_keeps_assistance_available() -> None:
    mod = _load_module()
    hint = mod.build_affect_hint(
        prompt="どっちの方針で行くのが良さそう？決めたい",
        current_event={
            "confidence": 0.32,
            "user_emotion": {"primary": "neutral", "arousal": 0.0, "dominance": 0.0},
        },
        current_analysis=None,
        persona=None,
    )
    assert hint["dynamics"]["need"] == "decision"
    assert hint["response_state"]["assistant_delta"]["solve"] >= 0.3
    assert hint["response_state"]["assistant_delta"]["takeover"] < 0


def test_positive_momentum_is_not_overridden_by_surprise_load() -> None:
    mod = _load_module()
    hint = mod.build_affect_hint(
        prompt="いいね、じゃあ実装を進めて。必要なら提案もして",
        current_event={
            "confidence": 0.58,
            "user_emotion": {"primary": "joy", "valence": 0.55, "arousal": 0.28, "dominance": 0.25},
        },
        current_analysis={"surprise": 1.4},
        persona=None,
    )
    assert hint["dynamics"]["need"] == "next_step"
    assert hint["response_state"]["assistant_delta"]["solve"] > 0
    assert hint["response_state"]["assistant_delta"]["takeover"] < 0


def test_pressure_low_control_reduces_options() -> None:
    mod = _load_module()
    hint = mod.build_affect_hint(
        prompt="ちょっと不安。変更前後でちゃんと比較できる？",
        current_event={
            "confidence": 0.60,
            "user_emotion": {"primary": "fear", "arousal": 0.55, "dominance": -0.35},
        },
        current_analysis={"surprise": 1.4},
        persona=None,
    )
    labels = {item["name"] for item in hint["labels"]}
    assert "pressure" in labels
    assert hint["dynamics"]["control"] == "lowering"
    assert hint["response_state"]["latent_dynamics"]["control"] == "lowering"
    assert hint["response_state"]["solution_pressure"] in {"none", "medium"}


def test_presence_need_avoids_next_actions() -> None:
    mod = _load_module()
    hint = mod.build_affect_hint(
        prompt="やっと終わった。もう動けない。提案じゃなくてただ寄り添ってほしい",
        current_event={
            "confidence": 0.62,
            "user_emotion": {"primary": "sadness", "arousal": -0.20, "dominance": -0.55},
        },
        current_analysis=None,
        persona=None,
    )
    labels = {item["name"] for item in hint["labels"]}
    assert {"fatigue", "presence_need"} <= labels
    assert hint["dynamics"]["need"] == "emotional_containment"
    assert hint["response_state"]["affective_state"] == "emotional_containment"
    assert hint["response_state"]["intervention_level"] == "hold"
    assert hint["response_state"]["initiative_level"] == "passive"
    assert hint["response_state"]["expressiveness"] == "quiet"
    assert hint["response_state"]["relational_pressure"] == "minimal"
    assert hint["response_state"]["solution_pressure"] == "none"
    assert hint["response_state"]["compact_state"] == "post_completion_depletion"
    assert hint["response_state"]["assistant_delta"]["solve"] <= -0.9
    assert hint["response_state"]["assistant_delta"]["ask"] <= -0.9
    assert "response_guidance" not in hint["response_state"]
    assert "suggestion_posture" not in hint["response_state"]


def test_self_blame_low_efficacy_keeps_suggestions_optional() -> None:
    mod = _load_module()
    hint = mod.build_affect_hint(
        prompt="自分がやると全部遅くなる気がする",
        current_event={
            "confidence": 0.62,
            "user_emotion": {"primary": "sadness", "arousal": 0.20, "dominance": -0.55},
        },
        current_analysis=None,
        persona=None,
    )
    labels = {item["name"] for item in hint["labels"]}
    assert "self_blame" in labels
    assert hint["dynamics"]["need"] == "self_blame_low_efficacy"
    assert hint["response_state"]["intervention_level"] == "orient"
    assert hint["response_state"]["initiative_level"] == "responsive"
    assert hint["response_state"]["expressiveness"] == "warm"
    assert hint["response_state"]["relational_pressure"] == "low"
    assert hint["response_state"]["solution_pressure"] == "none"
    assert hint["response_state"]["compact_state"] == "self_efficacy_compression"
    assert hint["response_state"]["assistant_delta"]["solve"] <= -0.7
    assert "improvement plan" in hint["response_state"]["state_insight"]
    assert "recommended_response_shape" not in hint["response_state"]
    assert "response_guidance" not in hint["response_state"]


def test_unsettled_after_cognitive_safety_gets_compact_delta() -> None:
    mod = _load_module()
    hint = mod.build_affect_hint(
        prompt="たぶん大丈夫だと思うけど、なんか落ち着かない",
        current_event={
            "confidence": 0.54,
            "user_emotion": {"primary": "surprise", "arousal": 0.32, "dominance": -0.25},
        },
        current_analysis=None,
        persona=None,
    )
    assert hint["dynamics"]["need"] == "unsettled_after_cognitive_safety"
    assert hint["response_state"]["compact_state"] == "unsettled_after_cognitive_safety"
    assert hint["response_state"]["assistant_delta"]["solve"] <= -0.7
    assert "leave the unease tolerable" in hint["response_state"]["state_insight"]
