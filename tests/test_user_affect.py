from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
USER_AFFECT_MODULE = (
    REPO_ROOT / ".agents" / "skills" / "shitsuji" / "scripts" / "user_affect.py"
)
@pytest.fixture(scope="module")
def user_affect():
    spec = importlib.util.spec_from_file_location("user_affect_under_test", USER_AFFECT_MODULE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["user_affect_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry(v: float, a: float, d: float, confidence: float = 0.8) -> dict:
    return {
        "valence": 0.1,
        "arousal": 0.1,
        "dominance": 0.1,
        "primary": "trust",
        "confidence": confidence,
        "user_emotion": {
            "valence": v,
            "arousal": a,
            "dominance": d,
            "primary": "neutral",
            "rationale": "seed",
        },
    }


def test_affect_dynamics_prior_uses_user_emotion(user_affect) -> None:
    prior = user_affect.affect_dynamics_prior(
        [_entry(0.1, 0.2, 0.0), _entry(0.2, 0.1, 0.1)],
        window=50,
    )
    assert prior["samples"] == 2
    assert prior["predicted_user_vad"]["valence"] == pytest.approx(0.068)
    assert prior["predicted_user_vad"]["arousal"] == pytest.approx(0.058)
    assert prior["dynamics"]["home_base"]["valence"] == pytest.approx(0.043)
    assert prior["dynamics"]["inertia"]["valence"] == pytest.approx(0.55)
    assert prior["reliability"] > 0.0


def test_sparse_prior_keeps_uncertainty_broad(user_affect) -> None:
    prior = user_affect.affect_dynamics_prior([_entry(0.8, 0.7, 0.6)], window=50)
    assert prior["samples"] == 1
    assert prior["predicted_user_vad"]["valence"] < 0.25
    assert prior["innovation_cov_diag"]["valence"] > 0.20
    assert prior["reliability"] < 0.05


def test_vfe_keeps_persistent_state_below_reversal(user_affect) -> None:
    entries = [
        _entry(-0.50, 0.60, -0.35),
        _entry(-0.55, 0.65, -0.40),
        _entry(-0.52, 0.62, -0.38),
        _entry(-0.58, 0.68, -0.42),
    ]
    prior = user_affect.affect_dynamics_prior(entries, window=50)
    repeated = user_affect.analyze_affective_free_energy(
        {"valence": -0.56, "arousal": 0.66, "dominance": -0.40},
        prior,
        confidence=0.8,
    )
    reversal = user_affect.analyze_affective_free_energy(
        {"valence": 0.45, "arousal": -0.15, "dominance": 0.20},
        prior,
        confidence=0.8,
    )
    assert repeated["variational_free_energy"]["total"] < reversal["variational_free_energy"]["total"]


def test_vfe_analysis_derives_regulation_and_response_control(user_affect) -> None:
    prior = user_affect.affect_dynamics_prior(
        [_entry(0.1, 0.2, 0.0), _entry(0.12, 0.18, 0.05), _entry(0.08, 0.22, 0.02)],
        window=50,
    )
    analysis = user_affect.analyze_affective_free_energy(
        {"valence": -0.55, "arousal": 0.72, "dominance": -0.50},
        prior,
        confidence=0.8,
    )
    assert analysis["variational_free_energy"]["total"] > 1.0
    assert analysis["state_hypotheses"]["low_control"] >= 0.5
    assert "regulation_mode" in analysis
    assert "response_surface" in analysis
    assert "analysis_summary" in analysis
    assert analysis["regulation_needs"]["increase_agency"] >= 0.5
    assert analysis["regulation_needs"]["effective_uncertainty"] <= analysis["regulation_needs"]["reduce_uncertainty"]
    assert analysis["response_control"]["structure"] > analysis["response_control"]["takeover"]


def test_uncertainty_preserves_exploration_but_limits_load(user_affect) -> None:
    prior = user_affect.affect_dynamics_prior(
        [_entry(0.05, 0.05, 0.05), _entry(0.10, 0.08, 0.05), _entry(0.0, 0.02, 0.0)],
        window=50,
    )
    analysis = user_affect.analyze_affective_free_energy(
        {"valence": -0.20, "arousal": 0.32, "dominance": -0.25},
        prior,
        confidence=0.54,
    )
    assert analysis["regulation_needs"]["reduce_uncertainty"] > 0.0
    assert analysis["regulation_needs"]["effective_uncertainty"] > 0.0
    assert analysis["regulation_needs"]["preserve_exploration"] > 0.0
    assert analysis["response_control"]["structure"] > analysis["response_control"]["takeover"]


def test_containment_mode_gates_uncertainty_away_from_questions(user_affect) -> None:
    prior = user_affect.affect_dynamics_prior([], window=50)
    base = user_affect.analyze_affective_free_energy(
        {"valence": 0.0, "arousal": 0.0, "dominance": 0.0},
        prior,
        confidence=0.32,
    )
    contained = user_affect.analyze_affective_free_energy(
        {"valence": 0.0, "arousal": 0.0, "dominance": 0.0},
        prior,
        confidence=0.32,
        interaction={
            "labels": [{"name": "presence_need", "score": 0.36}],
            "dynamics": {"need": "emotional_containment"},
        },
    )
    assert contained["regulation_mode"]["containment"] > base["regulation_mode"]["containment"]
    assert contained["regulation_needs"]["effective_uncertainty"] < base["regulation_needs"]["effective_uncertainty"]
    assert contained["response_surface"]["action"] == "user_led"
    assert contained["response_control"]["ask"] < base["response_control"]["ask"]
    assert contained["response_control"]["suggest"] <= 0.05


def test_agency_mode_keeps_self_blame_from_becoming_decomposition(user_affect) -> None:
    prior = user_affect.affect_dynamics_prior([], window=50)
    analysis = user_affect.analyze_affective_free_energy(
        {"valence": 0.0, "arousal": 0.0, "dominance": 0.0},
        prior,
        confidence=0.32,
        interaction={
            "labels": [{"name": "self_blame", "score": 0.34}],
            "dynamics": {"need": "self_blame_low_efficacy"},
        },
    )
    assert analysis["regulation_mode"]["agency"] >= analysis["regulation_mode"]["epistemic"]
    assert analysis["regulation_needs"]["effective_uncertainty"] <= 0.35
    assert analysis["response_surface"]["structure"] == "low"
    assert analysis["response_surface"]["action"] == "user_led"
    assert "reduced agency" in analysis["analysis_summary"]
    assert analysis["response_control"]["ask"] <= 0.30
    assert analysis["response_control"]["suggest"] <= 0.20


def test_verification_mode_preserves_epistemic_support(user_affect) -> None:
    prior = user_affect.affect_dynamics_prior([], window=50)
    analysis = user_affect.analyze_affective_free_energy(
        {"valence": -0.20, "arousal": 0.32, "dominance": -0.25},
        prior,
        confidence=0.54,
        interaction={
            "labels": [{"name": "verification_need", "score": 0.34}, {"name": "doubt", "score": 0.30}],
            "dynamics": {"need": "verification"},
        },
    )
    assert analysis["regulation_mode"]["epistemic"] > analysis["regulation_mode"]["containment"]
    assert analysis["regulation_needs"]["effective_uncertainty"] >= 0.35
    assert "verification or evidence" in analysis["analysis_summary"]
    assert analysis["response_surface"]["length"] == "detailed"
    assert analysis["response_surface"]["structure"] == "medium"
    assert analysis["response_surface"]["action"] == "user_led"
    assert analysis["response_control"]["structure"] > analysis["response_control"]["suggest"]
