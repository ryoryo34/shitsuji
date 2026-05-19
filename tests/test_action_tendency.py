"""Unit tests for the Frijda 6-primitive action tendency reference."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION_MODULE = REPO_ROOT / ".agents" / "skills" / "shitsuji" / "scripts" / "action_tendency.py"


@pytest.fixture(scope="module")
def at():
    spec = importlib.util.spec_from_file_location("action_tendency_under_test", ACTION_MODULE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["action_tendency_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestPrimitives:
    def test_six_primitives_defined(self, at):
        assert len(at.PRIMITIVES) == 6
        expected = {"approach", "attend", "inhibit", "avoid", "submit", "aggress"}
        assert set(at.PRIMITIVES.keys()) == expected

    def test_each_has_required_fields(self, at):
        for name, spec in at.PRIMITIVES.items():
            assert "vad_region" in spec
            assert "description" in spec
            assert "behavior_verb" in spec
            assert "min_expressive_range" in spec
            assert len(spec["vad_region"]) == 6  # vmin, vmax, amin, amax, dmin, dmax

    def test_description_returns_japanese_for_known(self, at):
        for name in at.all_names():
            d = at.description(name)
            assert d
            assert isinstance(d, str)

    def test_description_unknown_returns_none(self, at):
        assert at.description("not-a-primitive") is None

    def test_behavior_verb_is_short(self, at):
        for name in at.all_names():
            v = at.behavior_verb(name)
            assert v
            assert len(v) < 30


class TestCandidatesForVad:
    def test_positive_engaged_yields_approach(self, at):
        cands = at.candidates_for_vad(0.5, 0.4, 0.3, expressive_range=0.5)
        assert "approach" in cands

    def test_neutral_yields_attend(self, at):
        cands = at.candidates_for_vad(0.0, 0.0, 0.0, expressive_range=0.5)
        assert "attend" in cands

    def test_negative_high_arousal_high_dominance_yields_aggress_only_if_range_high(self, at):
        # Low expressive_range → aggress is gated out.
        low_range = at.candidates_for_vad(-0.5, 0.5, 0.4, expressive_range=0.3)
        assert "aggress" not in low_range
        # High range → aggress unlocked.
        high_range = at.candidates_for_vad(-0.5, 0.5, 0.4, expressive_range=0.85)
        assert "aggress" in high_range

    def test_submit_gated_by_expressive_range(self, at):
        # submit needs range ≥ 0.5
        low = at.candidates_for_vad(-0.4, 0.0, -0.5, expressive_range=0.3)
        assert "submit" not in low
        high = at.candidates_for_vad(-0.4, 0.0, -0.5, expressive_range=0.7)
        assert "submit" in high

    def test_returns_at_most_3(self, at):
        # No matter the input, we cap candidate count at 3.
        for v in [-1.0, 0.0, 1.0]:
            for a in [-1.0, 0.0, 1.0]:
                for d in [-1.0, 0.0, 1.0]:
                    cands = at.candidates_for_vad(v, a, d, expressive_range=1.0)
                    assert len(cands) <= 3

    def test_extreme_corner_returns_some_candidate_or_empty(self, at):
        # Either we match something or we don't — should never raise.
        cands = at.candidates_for_vad(-1.0, -1.0, -1.0, expressive_range=1.0)
        assert isinstance(cands, list)


class TestExpressiveRangeGating:
    def test_aggress_min_range_is_05(self, at):
        assert at.PRIMITIVES["aggress"]["min_expressive_range"] == 0.5

    def test_submit_min_range_is_05(self, at):
        assert at.PRIMITIVES["submit"]["min_expressive_range"] == 0.5

    def test_attend_no_gating(self, at):
        assert at.PRIMITIVES["attend"]["min_expressive_range"] == 0.0

    def test_approach_no_gating(self, at):
        assert at.PRIMITIVES["approach"]["min_expressive_range"] == 0.0
