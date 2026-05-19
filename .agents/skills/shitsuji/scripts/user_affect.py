#!/usr/bin/env python3
"""User-side affect dynamics + variational free-energy response control.

This module is intentionally deterministic and stdlib-only. It does not
try to infer the current utterance's emotion from text; the host LLM still
scores ``user_emotion``. Given prior ``user_emotion`` entries and an
observed VAD, this layer answers:

  - what affective trajectory should we have expected?
  - which latent state hypotheses remain plausible without asserting labels?
  - which VFE components are currently high?
  - what continuous response-control vector follows from that VFE profile?

This uses a small variational state-space approximation: a neutral affect
prior is updated into a personal home base, lightweight inertia/process
noise parameters are estimated with shrinkage, and a diagonal Kalman filter
predicts the next user-affect state. A compact variational-free-energy proxy
is then decomposed into prediction-error, uncertainty, affect-flux, and
control-cost components. The output is not a response category; it is a
continuous response-control surface for the host model.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SHITSUJI_DATA_DIR") or (SKILL_DIR / "data"))
HISTORY_FILE = DATA_DIR / "HISTORY.jsonl"

AXES = ("valence", "arousal", "dominance")
DEFAULT_VARIANCE = 0.20
VARIANCE_FLOOR = 0.04
PRIOR_MEAN = 0.0
PRIOR_STRENGTH = 4.0
PRIOR_ALPHA = 3.0
PRIOR_BETA = DEFAULT_VARIANCE * (PRIOR_ALPHA - 1.0)
PRIOR_INERTIA = 0.55
OBSERVATION_NOISE_BASE = 0.08
PROCESS_NOISE_FLOOR = 0.025


EMOTION_PROTOTYPES: dict[str, tuple[float, float, float]] = {
    "doubt": (-0.25, 0.35, -0.25),
    "confusion": (-0.35, 0.55, -0.55),
    "anxiety": (-0.65, 0.70, -0.60),
    "pressure": (-0.45, 0.65, -0.45),
    "frustration": (-0.55, 0.60, 0.25),
    "anger": (-0.65, 0.75, 0.55),
    "sadness": (-0.70, -0.35, -0.45),
    "exhaustion": (-0.55, -0.55, -0.55),
    "helplessness": (-0.65, -0.25, -0.75),
    "calm": (0.35, -0.35, 0.20),
    "relief": (0.45, -0.20, 0.10),
    "motivation": (0.55, 0.60, 0.55),
    "joy": (0.80, 0.50, 0.35),
    "curiosity": (0.35, 0.45, 0.25),
    "neutral": (0.0, 0.0, 0.0),
}

REFERENCE_VARIANCE = 0.20


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def read_history(path: Path = HISTORY_FILE) -> list[dict]:
    if not path.exists():
        return []
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    return entries


def _user_points(entries: list[dict], window: int | None) -> list[tuple[dict, float]]:
    if window is not None and window > 0:
        entries = entries[-window:]
    points: list[tuple[dict, float]] = []
    for entry in entries:
        ue = entry.get("user_emotion")
        if not isinstance(ue, dict):
            continue
        try:
            point = {axis: float(ue.get(axis, 0.0)) for axis in AXES}
        except (TypeError, ValueError):
            continue
        if not all(-1.0 <= point[axis] <= 1.0 for axis in AXES):
            continue
        try:
            confidence = float(entry.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        points.append((point, clamp(confidence, 0.1, 1.0)))
    return points


def _observation_variance(confidence: float) -> float:
    """Map affect-estimate confidence to observation noise.

    Hook auto-memory is intentionally low confidence, so it should nudge the
    latent state without collapsing uncertainty. High-confidence host scores
    can move the filter more strongly.
    """
    confidence = clamp(confidence, 0.1, 1.0)
    return clamp(OBSERVATION_NOISE_BASE / max(confidence, 0.2), 0.04, 0.45)


def _normal_gamma_posterior(
    values: list[tuple[float, float]],
) -> tuple[float, float, dict]:
    total_w = sum(weight for _, weight in values)
    if total_w <= 0.0:
        return PRIOR_MEAN, DEFAULT_VARIANCE, {
            "kappa": PRIOR_STRENGTH,
            "alpha": PRIOR_ALPHA,
            "beta": PRIOR_BETA,
            "effective_n": 0.0,
        }
    sample_mean = sum(value * weight for value, weight in values) / total_w
    weighted_ss = sum(weight * (value - sample_mean) ** 2 for value, weight in values)
    kappa_n = PRIOR_STRENGTH + total_w
    mean_n = (PRIOR_STRENGTH * PRIOR_MEAN + total_w * sample_mean) / kappa_n
    alpha_n = PRIOR_ALPHA + total_w / 2.0
    beta_n = (
        PRIOR_BETA
        + 0.5 * weighted_ss
        + (PRIOR_STRENGTH * total_w * (sample_mean - PRIOR_MEAN) ** 2)
        / (2.0 * kappa_n)
    )
    predictive_var = beta_n * (kappa_n + 1.0) / max(alpha_n * kappa_n, 1e-9)
    return mean_n, clamp(predictive_var, VARIANCE_FLOOR, 1.0), {
        "kappa": round(kappa_n, 3),
        "alpha": round(alpha_n, 3),
        "beta": round(beta_n, 3),
        "effective_n": round(total_w, 3),
    }


def _estimate_axis_dynamics(
    points: list[tuple[dict, float]],
    axis: str,
    home: float,
    home_var: float,
    effective_n: float,
) -> tuple[float, float]:
    release = clamp((effective_n - 5.0) / 25.0, 0.0, 1.0)
    if len(points) < 2:
        return PRIOR_INERTIA, clamp(home_var * 0.75, PROCESS_NOISE_FLOOR, 0.6)

    numerator = 0.0
    denominator = 0.0
    residuals: list[tuple[float, float]] = []
    for (prev, prev_w), (cur, cur_w) in zip(points, points[1:]):
        pair_w = math.sqrt(prev_w * cur_w)
        prev_dev = float(prev[axis]) - home
        cur_dev = float(cur[axis]) - home
        numerator += pair_w * prev_dev * cur_dev
        denominator += pair_w * prev_dev * prev_dev
    empirical_phi = numerator / denominator if denominator > 1e-9 else PRIOR_INERTIA
    empirical_phi = clamp(empirical_phi, 0.05, 0.92)
    phi = (1.0 - release) * PRIOR_INERTIA + release * empirical_phi

    for (prev, prev_w), (cur, cur_w) in zip(points, points[1:]):
        pair_w = math.sqrt(prev_w * cur_w)
        residual = (float(cur[axis]) - home) - phi * (float(prev[axis]) - home)
        residuals.append((residual, pair_w))
    total_pair_w = sum(weight for _, weight in residuals)
    empirical_q = (
        sum(weight * residual * residual for residual, weight in residuals) / total_pair_w
        if total_pair_w > 1e-9
        else home_var
    )
    q = (1.0 - release) * home_var * 0.75 + release * empirical_q
    return round(phi, 3), clamp(q, PROCESS_NOISE_FLOOR, 0.6)


def affect_dynamics_prior(
    entries: list[dict],
    *,
    window: int | None = 50,
) -> dict:
    """Return a variational affect-dynamics prior for the next observation.

    The implementation is deliberately diagonal and stdlib-only. It follows
    the affect-dynamics idea of a personal home base, inertia, and variability:
    a Normal-Gamma posterior estimates the home base, shrunk AR(1)-style
    dynamics estimate inertia and process noise, then a Kalman filter produces
    the predicted next VAD state. ``mean`` and ``cov_diag`` preserve the legacy
    The returned fields intentionally avoid baseline/surprise terminology:
    ``predicted_user_vad`` is the next-state prior and ``predicted_cov_diag``
    is the dynamics uncertainty before adding current observation noise.
    """
    points = _user_points(entries, window)
    if not points:
        return {
            "predicted_user_vad": {axis: 0.0 for axis in AXES},
            "predicted_cov_diag": {axis: DEFAULT_VARIANCE for axis in AXES},
            "samples": 0,
            "effective_n": 0.0,
            "reliability": 0.0,
            "dynamics": {
                "home_base": {axis: 0.0 for axis in AXES},
                "home_base_cov_diag": {axis: DEFAULT_VARIANCE for axis in AXES},
                "inertia": {axis: PRIOR_INERTIA for axis in AXES},
                "process_noise_diag": {axis: DEFAULT_VARIANCE * 0.75 for axis in AXES},
                "model": "diagonal_variational_affect_dynamics_vfe",
            },
            "posterior": {
                axis: {
                    "kappa": PRIOR_STRENGTH,
                    "alpha": PRIOR_ALPHA,
                    "beta": PRIOR_BETA,
                    "effective_n": 0.0,
                }
                for axis in AXES
            },
            "note": "no prior user_emotion entries; using neutral dynamics prior",
        }

    effective_n = sum(weight for _, weight in points)
    home_base = {}
    home_base_cov = {}
    posterior = {}
    for axis in AXES:
        home, home_var, post = _normal_gamma_posterior(
            [(float(point[axis]), weight) for point, weight in points]
        )
        home_base[axis] = home
        home_base_cov[axis] = home_var
        posterior[axis] = post

    inertia = {}
    process_noise = {}
    filtered_mean = {}
    filtered_cov = {}
    predicted_mean = {}
    predicted_state_cov = {}
    innovation_cov = {}
    for axis in AXES:
        phi, q = _estimate_axis_dynamics(
            points, axis, home_base[axis], home_base_cov[axis], effective_n
        )
        inertia[axis] = phi
        process_noise[axis] = q
        m = home_base[axis]
        p = home_base_cov[axis]
        for point, weight in points:
            pred_m = home_base[axis] + phi * (m - home_base[axis])
            pred_p = phi * phi * p + q
            obs_var = _observation_variance(weight)
            kalman_gain = pred_p / max(pred_p + obs_var, 1e-9)
            m = pred_m + kalman_gain * (float(point[axis]) - pred_m)
            p = clamp((1.0 - kalman_gain) * pred_p, VARIANCE_FLOOR / 4.0, 1.0)
        next_m = home_base[axis] + phi * (m - home_base[axis])
        next_p = clamp(phi * phi * p + q, VARIANCE_FLOOR, 1.0)
        sparse_state_release = clamp(effective_n / 3.2, 0.0, 1.0)
        next_m = (
            (1.0 - sparse_state_release) * home_base[axis]
            + sparse_state_release * next_m
        )
        filtered_mean[axis] = m
        filtered_cov[axis] = p
        predicted_mean[axis] = next_m
        predicted_state_cov[axis] = next_p
        innovation_cov[axis] = clamp(next_p + _observation_variance(0.7), VARIANCE_FLOOR, 1.0)

    reliability = clamp(effective_n / (effective_n + 20.0), 0.0, 1.0)
    return {
        "predicted_user_vad": {axis: round(predicted_mean[axis], 3) for axis in AXES},
        "predicted_cov_diag": {axis: round(predicted_state_cov[axis], 3) for axis in AXES},
        "innovation_cov_diag": {axis: round(innovation_cov[axis], 3) for axis in AXES},
        "samples": len(points),
        "effective_n": round(effective_n, 3),
        "reliability": round(reliability, 3),
        "dynamics": {
            "home_base": {axis: round(home_base[axis], 3) for axis in AXES},
            "home_base_cov_diag": {axis: round(home_base_cov[axis], 3) for axis in AXES},
            "filtered_state": {axis: round(filtered_mean[axis], 3) for axis in AXES},
            "filtered_cov_diag": {axis: round(filtered_cov[axis], 3) for axis in AXES},
            "inertia": inertia,
            "process_noise_diag": {axis: round(process_noise[axis], 3) for axis in AXES},
            "model": "diagonal_variational_affect_dynamics_vfe",
        },
        "posterior": posterior,
        "note": "confidence-weighted diagonal VB/Kalman affect dynamics prior",
    }


def _level(value: float, medium: float, high: float) -> str:
    if value >= high:
        return "high"
    if value >= medium:
        return "medium"
    return "low"


def _positive(value: float) -> float:
    return clamp(value, 0.0, 1.0)


def _negative(value: float) -> float:
    return clamp(-value, 0.0, 1.0)


def state_hypotheses(observed: dict, vfe_components: dict, precision: float) -> dict:
    """Return non-exclusive latent hypotheses used only for control math."""
    v = float(observed["valence"])
    a = float(observed["arousal"])
    d = float(observed["dominance"])
    low_valence = _negative(v)
    high_arousal = _positive(a)
    low_control = _negative(d)
    uncertainty = clamp(float(vfe_components.get("uncertainty", 0.0)), 0.0, 1.0)
    control_cost = clamp(float(vfe_components.get("control_cost", 0.0)), 0.0, 1.0)
    affect_flux = clamp(float(vfe_components.get("affect_flux", 0.0)), 0.0, 1.0)
    overload = clamp(high_arousal * 0.45 + low_control * 0.35 + control_cost * 0.55, 0.0, 1.0)
    exploration_potential = clamp(
        0.30 + _positive(a) * 0.25 + (1.0 - abs(v)) * 0.25 + uncertainty * 0.25
        ,
        0.0,
        1.0,
    )
    hypotheses = {
        "anxiety": low_valence * high_arousal * low_control,
        "confusion": uncertainty * max(0.25, high_arousal) * max(0.25, low_control),
        "curiosity": exploration_potential * (1.0 - overload * 0.65),
        "low_control": low_control,
        "overload": overload,
        "affect_flux": affect_flux,
        "low_precision": 1.0 - precision,
    }
    return {name: round(clamp(value, 0.0, 1.0), 3) for name, value in hypotheses.items()}


def variational_free_energy(
    observed_vad: dict,
    prior: dict,
    *,
    confidence: float,
) -> dict:
    predicted = prior["predicted_user_vad"]
    dynamics = prior.get("dynamics") or {}
    filtered = dynamics.get("filtered_state") or dynamics.get("home_base") or predicted
    predicted_cov = prior.get("predicted_cov_diag", {})
    obs_var = _observation_variance(confidence)
    innovation = {
        axis: float(observed_vad[axis]) - float(predicted.get(axis, 0.0))
        for axis in AXES
    }
    innovation_cov = {
        axis: float(predicted_cov.get(axis, DEFAULT_VARIANCE)) + obs_var
        for axis in AXES
    }
    prediction_error = 0.5 * sum(
        (innovation[axis] ** 2) / max(innovation_cov[axis], VARIANCE_FLOOR)
        for axis in AXES
    )
    uncertainty = 0.5 * sum(
        max(0.0, math.log(max(innovation_cov[axis], VARIANCE_FLOOR) / REFERENCE_VARIANCE))
        for axis in AXES
    )
    affect_flux = math.sqrt(
        1.0 * (float(observed_vad["valence"]) - float(filtered.get("valence", 0.0))) ** 2
        + 1.2 * (float(observed_vad["arousal"]) - float(filtered.get("arousal", 0.0))) ** 2
        + 1.1 * (float(observed_vad["dominance"]) - float(filtered.get("dominance", 0.0))) ** 2
    )
    control_cost = (
        0.25 * _negative(float(observed_vad["dominance"]))
        + 0.15 * _positive(float(observed_vad["arousal"]))
        + 0.15 * _negative(float(observed_vad["valence"]))
    )
    components = {
        "prediction_error": prediction_error,
        "uncertainty": uncertainty,
        "affect_flux": affect_flux,
        "control_cost": control_cost,
    }
    weighted = {
        "prediction_error": prediction_error,
        "uncertainty": 0.35 * uncertainty,
        "affect_flux": 0.55 * affect_flux,
        "control_cost": 0.45 * control_cost,
    }
    total = sum(weighted.values())
    dominant = max(weighted.items(), key=lambda item: item[1])[0]
    target = {
        "prediction_error": "stabilize_prediction_error",
        "uncertainty": "reduce_uncertainty",
        "affect_flux": "dampen_affective_velocity",
        "control_cost": "reduce_control_load",
    }[dominant]
    avg_cov = sum(max(innovation_cov[axis], VARIANCE_FLOOR) for axis in AXES) / len(AXES)
    reliability = float(prior.get("reliability", 0.0))
    certainty = clamp(1.0 - avg_cov / 0.75, 0.0, 1.0)
    precision = clamp(confidence * (0.35 + 0.45 * reliability + 0.20 * certainty), 0.05, 1.0)
    return {
        "total": round(total, 3),
        "components": {name: round(value, 3) for name, value in components.items()},
        "weighted_components": {name: round(value, 3) for name, value in weighted.items()},
        "dominant_component": dominant,
        "regulation_target": target,
        "precision": round(precision, 3),
        "innovation": {axis: round(innovation[axis], 3) for axis in AXES},
        "innovation_cov_diag": {axis: round(innovation_cov[axis], 3) for axis in AXES},
    }


def _interaction_label_scores(interaction: dict | None) -> dict[str, float]:
    if not isinstance(interaction, dict):
        return {}
    labels = interaction.get("labels")
    if not isinstance(labels, list):
        return {}
    scores: dict[str, float] = {}
    for item in labels:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        try:
            score = float(item.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        scores[name] = clamp(score, 0.0, 1.0)
    return scores


def _interaction_need(interaction: dict | None) -> str:
    if not isinstance(interaction, dict):
        return ""
    dynamics = interaction.get("dynamics")
    if isinstance(dynamics, dict):
        return str(dynamics.get("need") or "")
    response_state = interaction.get("response_state")
    if isinstance(response_state, dict):
        return str(response_state.get("affective_state") or "")
    return ""


def regulation_mode(
    hypotheses: dict,
    vfe: dict,
    observed: dict,
    interaction: dict | None = None,
) -> dict:
    """Gate VFE components into abstract intervention modes.

    ``state_hypotheses`` says what might be true. ``regulation_mode`` says how
    intervention should happen, so raw uncertainty does not always become
    questions, explanation, or decomposition.
    """
    components = vfe["components"]
    uncertainty = clamp(float(components.get("uncertainty", 0.0)), 0.0, 1.0)
    control_cost = clamp(float(components.get("control_cost", 0.0)), 0.0, 1.0)
    affect_flux = clamp(float(components.get("affect_flux", 0.0)), 0.0, 1.0)
    low_control = float(hypotheses.get("low_control", 0.0))
    overload = float(hypotheses.get("overload", 0.0))
    curiosity = float(hypotheses.get("curiosity", 0.0))
    low_precision = float(hypotheses.get("low_precision", 0.0))
    low_valence = _negative(float(observed["valence"]))
    low_arousal = _negative(float(observed["arousal"]))

    label_scores = _interaction_label_scores(interaction)
    need = _interaction_need(interaction)
    verification = max(label_scores.get("verification_need", 0.0), label_scores.get("doubt", 0.0))
    confusion = label_scores.get("confusion", 0.0)
    momentum = label_scores.get("momentum", 0.0)
    fatigue = label_scores.get("fatigue", 0.0)
    presence = label_scores.get("presence_need", 0.0)
    self_blame = label_scores.get("self_blame", 0.0)
    pressure = label_scores.get("pressure", 0.0)

    epistemic = (
        0.22
        + 0.42 * max(uncertainty, float(hypotheses.get("confusion", 0.0)))
        + 0.50 * verification
        + 0.35 * confusion
        + 0.20 * curiosity
    )
    agency = 0.16 + 0.48 * max(low_control, control_cost) + 0.58 * self_blame + 0.20 * pressure
    depletion = 0.10 + 0.35 * overload + 0.35 * low_valence * max(low_arousal, 0.25) + 0.62 * fatigue
    containment = (
        0.10
        + 0.38 * affect_flux
        + 0.30 * overload
        + 0.62 * presence
        + 0.36 * pressure * low_precision
    )

    if need in {"verification", "decision"}:
        epistemic += 0.35
        containment -= 0.10
    elif need == "clarification":
        epistemic += 0.18
    elif need == "next_step":
        epistemic += 0.12
        agency += 0.15
        depletion -= 0.15
    elif need == "load_reduction":
        depletion += 0.32
        containment += 0.16
        epistemic -= 0.18
    elif need == "emotional_containment":
        containment += 0.42
        depletion += 0.18
        epistemic -= 0.22
    elif need in {"self_blame_low_efficacy", "unsettled_after_cognitive_safety"}:
        agency += 0.30
        containment += 0.25
        epistemic -= 0.20

    if momentum >= 0.20:
        depletion -= 0.10
        containment -= 0.08

    modes = {
        "epistemic": epistemic,
        "agency": agency,
        "depletion": depletion,
        "containment": containment,
    }
    return {name: round(clamp(value, 0.0, 1.0), 3) for name, value in modes.items()}


def regulation_needs(hypotheses: dict, vfe: dict, observed: dict, modes: dict | None = None) -> dict:
    components = vfe["components"]
    uncertainty = clamp(components.get("uncertainty", 0.0), 0.0, 1.0)
    affect_flux = clamp(components.get("affect_flux", 0.0), 0.0, 1.0)
    control_cost = clamp(components.get("control_cost", 0.0), 0.0, 1.0)
    high_arousal_low_control = _positive(float(observed["arousal"])) * _negative(float(observed["dominance"]))
    if modes is None:
        modes = regulation_mode(hypotheses, vfe, observed)
    epistemic = float(modes.get("epistemic", 0.0))
    agency = float(modes.get("agency", 0.0))
    depletion = float(modes.get("depletion", 0.0))
    containment = float(modes.get("containment", 0.0))
    raw_uncertainty = max(uncertainty, float(hypotheses.get("confusion", 0.0)))
    effective_uncertainty = (
        raw_uncertainty
        * epistemic
        * (1.0 - 0.72 * depletion)
        * (1.0 - 0.62 * containment)
        * (1.0 - 0.45 * agency)
    )
    exploration_gate = (
        (1.0 - float(hypotheses.get("overload", 0.0)) * 0.6)
        * (1.0 - 0.55 * depletion)
        * (1.0 - 0.45 * containment)
        * (1.0 - 0.35 * agency)
    )
    needs = {
        "reduce_uncertainty": raw_uncertainty,
        "effective_uncertainty": effective_uncertainty,
        "increase_agency": max(float(hypotheses.get("low_control", 0.0)), control_cost, agency * 0.85),
        "dampen_affective_velocity": affect_flux,
        "preserve_exploration": float(hypotheses.get("curiosity", 0.0)) * exploration_gate,
        "limit_cognitive_load": max(float(hypotheses.get("overload", 0.0)), control_cost, high_arousal_low_control, depletion * 0.85, containment * 0.55),
    }
    return {name: round(clamp(value, 0.0, 1.0), 3) for name, value in needs.items()}


def response_control(needs: dict, hypotheses: dict, vfe: dict, modes: dict | None = None) -> dict:
    ru = float(needs.get("effective_uncertainty", needs.get("reduce_uncertainty", 0.0)))
    ia = float(needs.get("increase_agency", 0.0))
    dav = float(needs.get("dampen_affective_velocity", 0.0))
    pe = float(needs.get("preserve_exploration", 0.0))
    lcl = float(needs.get("limit_cognitive_load", 0.0))
    modes = modes or {}
    epistemic = float(modes.get("epistemic", 0.0))
    agency = float(modes.get("agency", 0.0))
    depletion = float(modes.get("depletion", 0.0))
    containment = float(modes.get("containment", 0.0))
    low_control = float(hypotheses.get("low_control", 0.0))
    overload = float(hypotheses.get("overload", 0.0))
    control = {
        "ask": 0.20 + 0.35 * pe + 0.20 * ru - 0.30 * lcl - 0.18 * containment - 0.12 * agency,
        "explain": 0.20 + 0.50 * ru + 0.15 * epistemic - 0.25 * lcl - 0.15 * containment - 0.08 * depletion,
        "suggest": 0.18 + 0.18 * ia - 0.25 * lcl - 0.18 * low_control - 0.20 * containment - 0.18 * depletion,
        "takeover": 0.35 - 0.45 * ia - 0.30 * pe + 0.15 * overload,
        "verbosity": 0.45 + 0.25 * ru - 0.45 * lcl,
        "warmth": 0.35 + 0.30 * dav + 0.20 * float(vfe["components"].get("control_cost", 0.0)),
        "structure": 0.28 + 0.38 * ru + 0.07 * epistemic + 0.26 * lcl - 0.16 * containment - 0.10 * depletion,
    }
    return {name: round(clamp(value, 0.0, 1.0), 3) for name, value in control.items()}


def _band(value: float, low_cutoff: float = 0.34, high_cutoff: float = 0.67) -> str:
    if value >= high_cutoff:
        return "high"
    if value >= low_cutoff:
        return "medium"
    return "low"


def response_surface(needs: dict, control: dict, modes: dict, interaction: dict | None = None) -> dict:
    """Return the compact surface contract intended for LLM-visible guidance."""
    need = _interaction_need(interaction)
    effective_uncertainty = float(needs.get("effective_uncertainty", 0.0))
    load = float(needs.get("limit_cognitive_load", 0.0))
    agency = float(modes.get("agency", 0.0))
    depletion = float(modes.get("depletion", 0.0))
    containment = float(modes.get("containment", 0.0))
    epistemic = float(modes.get("epistemic", 0.0))
    shortness = clamp(
        0.20
        + 0.32 * load
        + 0.26 * depletion
        + 0.24 * containment
        + 0.18 * agency
        - 0.22 * epistemic,
        0.0,
        1.0,
    )
    detail = clamp(
        0.20
        + 0.48 * epistemic
        + 0.32 * effective_uncertainty
        - 0.28 * load
        - 0.22 * containment
        - 0.16 * depletion,
        0.0,
        1.0,
    )
    followup = clamp(
        float(control.get("ask", 0.0)) * (1.0 - 0.45 * containment) * (1.0 - 0.35 * depletion),
        0.0,
        1.0,
    )
    structure = clamp(
        float(control.get("structure", 0.0))
        * (0.40 + 0.60 * effective_uncertainty)
        * (1.0 - 0.40 * containment)
        * (1.0 - 0.30 * agency),
        0.0,
        1.0,
    )
    action_score = clamp(
        (float(control.get("suggest", 0.0)) + 0.20 * float(control.get("takeover", 0.0)))
        * (1.0 - 0.45 * containment)
        * (1.0 - 0.40 * depletion)
        * (1.0 - 0.30 * agency),
        0.0,
        1.0,
    )
    if need in {"verification", "decision"}:
        action = "user_led"
    elif agency >= 0.55 or containment >= 0.55 or depletion >= 0.45:
        action = "user_led"
    elif action_score >= 0.45:
        action = "suggest"
    elif action_score <= 0.12:
        action = "avoid"
    else:
        action = "user_led"
    if need == "verification":
        length = "detailed"
    elif shortness >= 0.50:
        length = "short"
    else:
        length = "medium"
    structure_band = _band(structure)
    if need in {"verification", "decision"} and structure_band == "low":
        structure_band = "medium"
    elif need not in {"verification", "decision"} and epistemic >= 0.70 and effective_uncertainty >= 0.35:
        structure_band = "low"
        if followup < 0.67:
            followup = min(followup, 0.33)
    return {
        "length": length,
        "followup": _band(followup),
        "structure": structure_band,
        "action": action,
    }


def analysis_summary(needs: dict, modes: dict, surface: dict, interaction: dict | None = None) -> str:
    """Short natural-language rationale for the LLM-visible guidance."""
    need = _interaction_need(interaction)
    effective_uncertainty = float(needs.get("effective_uncertainty", 0.0))
    epistemic = float(modes.get("epistemic", 0.0))
    agency = float(modes.get("agency", 0.0))
    depletion = float(modes.get("depletion", 0.0))
    containment = float(modes.get("containment", 0.0))
    if need == "verification":
        return "user input appears to ask for verification or evidence; preserve file/task grounding and reduce uncertainty with concise structure."
    if epistemic >= 0.70 and effective_uncertainty >= 0.35:
        return "user input appears uncertain but not necessarily task-verification oriented; keep structure light and do not over-classify the feeling."
    if agency >= 0.55:
        return "user input appears to involve reduced agency; avoid treating it as a decomposition or improvement-planning task."
    if containment >= 0.55 or depletion >= 0.45:
        return "user input appears to need containment or low cognitive load; hold uncertainty lightly and avoid extra tasks."
    if surface.get("length") == "short" and surface.get("action") in {"avoid", "user_led"}:
        return "user input appears to need a compact response; avoid adding unsolicited next steps."
    return "user input appears stable enough for a normal calibrated response."


def analyze_affective_free_energy(
    observed: dict,
    prior: dict,
    *,
    confidence: float = 0.7,
    interaction: dict | None = None,
) -> dict:
    observed_vad = {axis: float(observed[axis]) for axis in AXES}
    vfe = variational_free_energy(observed_vad, prior, confidence=confidence)
    hypotheses = state_hypotheses(observed_vad, vfe["components"], vfe["precision"])
    modes = regulation_mode(hypotheses, vfe, observed_vad, interaction=interaction)
    needs = regulation_needs(hypotheses, vfe, observed_vad, modes)
    control = response_control(needs, hypotheses, vfe, modes)
    surface = response_surface(needs, control, modes, interaction=interaction)
    analysis = {
        "observed_user_vad": {axis: round(observed_vad[axis], 3) for axis in AXES},
        "predicted_user_vad": prior["predicted_user_vad"],
        "variational_free_energy": vfe,
        "state_hypotheses": hypotheses,
        "regulation_mode": modes,
        "regulation_needs": needs,
        "response_control": control,
        "response_surface": surface,
        "analysis_summary": analysis_summary(needs, modes, surface, interaction=interaction),
    }
    return analysis


def compact_runtime_hint(prior: dict) -> dict:
    """Small block suitable for every-turn hook injection."""
    dynamics = prior.get("dynamics") or {}
    return {
        "affect_prior_mean": {axis: prior["predicted_user_vad"][axis] for axis in AXES},
        "affect_prior_cov_diag": {axis: prior["predicted_cov_diag"][axis] for axis in AXES},
        "filtered_state": dynamics.get("filtered_state", {}),
        "samples": prior["samples"],
        "effective_n": prior.get("effective_n", prior["samples"]),
        "reliability": prior["reliability"],
        "free_energy_model": "diagonal_vfe_proxy",
        "dynamics": {
            "model": dynamics.get("model", "diagonal_variational_affect_dynamics_vfe"),
            "inertia": dynamics.get("inertia", {}),
        },
        "host_task": (
            "Score current user_emotion; compute VFE components; derive regulation_needs "
            "and continuous response_control. Do not state hypotheses as facts."
        ),
    }


def compute_user_model(
    *,
    window: int | None = 50,
    observed: dict | None = None,
    confidence: float = 0.7,
) -> dict:
    prior = affect_dynamics_prior(read_history(), window=window)
    out = {
        "affect_prior": prior,
        "runtime_hint": compact_runtime_hint(prior),
    }
    if observed is not None:
        out["current_analysis"] = analyze_affective_free_energy(
            observed, prior, confidence=confidence
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=50)
    ap.add_argument(
        "--observed-vad",
        nargs=3,
        type=float,
        metavar=("V", "A", "D"),
        help="optional current user VAD; emits prediction error + policy",
    )
    ap.add_argument("--confidence", type=float, default=0.7)
    args = ap.parse_args()

    observed = None
    if args.observed_vad is not None:
        observed = {
            "valence": args.observed_vad[0],
            "arousal": args.observed_vad[1],
            "dominance": args.observed_vad[2],
        }
    json.dump(
        compute_user_model(window=args.window, observed=observed, confidence=args.confidence),
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
