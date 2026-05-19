#!/usr/bin/env python3
"""Compare legacy rolling prior with current VFE affect-dynamics control.

The benchmark is deterministic and stdlib-only. It reports three practical
dimensions for the hook path:

- processing time for prior + current VFE/control analysis
- approximate JSON/token footprint of the runtime hint
- behavior checks that stand in for sparse-data accuracy until a labeled
  longitudinal dataset exists
"""

from __future__ import annotations

import json
import statistics
import sys
import time

import user_affect

AXES = user_affect.AXES
DEFAULT_VARIANCE = user_affect.DEFAULT_VARIANCE
VARIANCE_FLOOR = user_affect.VARIANCE_FLOOR


def entry(v: float, a: float, d: float, confidence: float = 0.8) -> dict:
    return {
        "confidence": confidence,
        "user_emotion": {
            "valence": v,
            "arousal": a,
            "dominance": d,
            "primary": "neutral",
            "rationale": "benchmark fixture",
        },
    }


def legacy_baseline_distribution(entries: list[dict], window: int | None = 50) -> dict:
    points = user_affect._user_points(entries, window)  # noqa: SLF001 - benchmark parity
    if not points:
        return {
            "mean": {axis: 0.0 for axis in AXES},
            "cov_diag": {axis: DEFAULT_VARIANCE for axis in AXES},
            "samples": 0,
            "reliability": 0.0,
        }
    total_w = sum(weight for _, weight in points)
    mean = {
        axis: sum(point[axis] * weight for point, weight in points) / total_w
        for axis in AXES
    }
    cov_diag = {}
    for axis in AXES:
        var = sum(
            weight * (point[axis] - mean[axis]) ** 2 for point, weight in points
        ) / total_w
        sparse_blend = 1.0 / max(len(points), 1)
        var = (1.0 - sparse_blend) * var + sparse_blend * DEFAULT_VARIANCE
        cov_diag[axis] = user_affect.clamp(var, VARIANCE_FLOOR, 1.0)
    reliability = user_affect.clamp(len(points) / 20.0, 0.0, 1.0) * user_affect.clamp(
        total_w / len(points), 0.1, 1.0
    )
    return {
        "mean": {axis: round(mean[axis], 3) for axis in AXES},
        "cov_diag": {axis: round(cov_diag[axis], 3) for axis in AXES},
        "samples": len(points),
        "reliability": round(reliability, 3),
    }


SCENARIOS = {
    "empty_prior": {
        "history": [],
        "observed": {"valence": -0.20, "arousal": 0.32, "dominance": -0.25},
        "confidence": 0.54,
    },
    "sparse_single_positive_then_neutral": {
        "history": [entry(0.80, 0.70, 0.60)],
        "observed": {"valence": 0.0, "arousal": 0.0, "dominance": 0.0},
        "confidence": 0.8,
    },
    "persistent_distress": {
        "history": [
            entry(-0.50, 0.60, -0.35),
            entry(-0.55, 0.65, -0.40),
            entry(-0.52, 0.62, -0.38),
            entry(-0.58, 0.68, -0.42),
        ],
        "observed": {"valence": -0.56, "arousal": 0.66, "dominance": -0.40},
        "confidence": 0.8,
    },
    "reversal_after_distress": {
        "history": [
            entry(-0.50, 0.60, -0.35),
            entry(-0.55, 0.65, -0.40),
            entry(-0.52, 0.62, -0.38),
            entry(-0.58, 0.68, -0.42),
        ],
        "observed": {"valence": 0.45, "arousal": -0.15, "dominance": 0.20},
        "confidence": 0.8,
    },
    "calm_history_clear_outlier": {
        "history": [
            entry(0.10, 0.05, 0.05),
            entry(0.08, 0.02, 0.04),
            entry(0.12, 0.08, 0.06),
            entry(0.05, 0.04, 0.03),
            entry(0.10, 0.06, 0.05),
        ],
        "observed": {"valence": -0.65, "arousal": 0.72, "dominance": -0.55},
        "confidence": 0.8,
    },
}


def run_once(kind: str, scenario: dict) -> dict:
    if kind == "legacy":
        legacy = legacy_baseline_distribution(scenario["history"])
        prior = {
            "predicted_user_vad": legacy["mean"],
            "predicted_cov_diag": legacy["cov_diag"],
            "samples": legacy["samples"],
            "reliability": legacy["reliability"],
            "dynamics": {"filtered_state": legacy["mean"]},
        }
        hint = {
            "affect_prior_mean": prior["predicted_user_vad"],
            "affect_prior_cov_diag": prior["predicted_cov_diag"],
            "samples": prior["samples"],
            "reliability": prior["reliability"],
        }
    else:
        prior = user_affect.affect_dynamics_prior(scenario["history"])
        hint = user_affect.compact_runtime_hint(prior)
    analysis = user_affect.analyze_affective_free_energy(
        scenario["observed"], prior, confidence=scenario["confidence"]
    )
    hint_json = json.dumps(hint, ensure_ascii=False, separators=(",", ":"))
    return {
        "prior": prior,
        "analysis": analysis,
        "hint_bytes": len(hint_json.encode("utf-8")),
        "hint_token_estimate": round(len(hint_json) / 4.0, 1),
    }


def time_kind(kind: str, scenario: dict, iterations: int) -> tuple[float, dict]:
    samples: list[float] = []
    result = {}
    for _ in range(iterations):
        start = time.perf_counter()
        result = run_once(kind, scenario)
        samples.append((time.perf_counter() - start) * 1000.0)
    return statistics.median(samples), result


def behavior_checks(results: dict) -> dict:
    legacy = results["legacy"]
    dynamics = results["dynamics"]
    checks = {
        "sparse_vfe_lower_than_legacy": dynamics["sparse_single_positive_then_neutral"]["vfe_total"]
        < legacy["sparse_single_positive_then_neutral"]["vfe_total"],
        "persistent_vfe_below_reversal": dynamics["persistent_distress"]["vfe_total"]
        < dynamics["reversal_after_distress"]["vfe_total"],
        "clear_outlier_high_vfe": dynamics["calm_history_clear_outlier"]["vfe_total"] >= 1.0,
        "response_control_limits_takeover_under_load": dynamics["calm_history_clear_outlier"]["response_control"]["takeover"]
        < dynamics["calm_history_clear_outlier"]["response_control"]["structure"],
    }
    checks["score"] = f"{sum(checks.values())}/{len(checks)}"
    return checks


def main() -> None:
    iterations = 2000
    report: dict = {"iterations": iterations, "scenarios": {}, "legacy": {}, "dynamics": {}}
    for name, scenario in SCENARIOS.items():
        report["scenarios"][name] = {
            "history_n": len(scenario["history"]),
            "observed": scenario["observed"],
        }
        for kind in ("legacy", "dynamics"):
            elapsed_ms, result = time_kind(kind, scenario, iterations)
            vfe = result["analysis"]["variational_free_energy"]
            report[kind][name] = {
                "median_ms": round(elapsed_ms, 4),
                "vfe_total": vfe["total"],
                "vfe_components": vfe["components"],
                "dominant_component": vfe["dominant_component"],
                "regulation_target": vfe["regulation_target"],
                "precision": vfe["precision"],
                "hypotheses": result["analysis"]["state_hypotheses"],
                "regulation_mode": result["analysis"].get("regulation_mode", {}),
                "regulation_needs": result["analysis"]["regulation_needs"],
                "response_control": result["analysis"]["response_control"],
                "response_surface": result["analysis"].get("response_surface", {}),
                "analysis_summary": result["analysis"].get("analysis_summary", ""),
                "hint_bytes": result["hint_bytes"],
                "hint_token_estimate": result["hint_token_estimate"],
                "affect_prior_mean": result["prior"]["predicted_user_vad"],
                "affect_prior_cov_diag": result["prior"]["predicted_cov_diag"],
            }
    report["behavior_checks"] = behavior_checks(report)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
