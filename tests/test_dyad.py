"""Unit tests for the Plutchik 24-dyad lookup module."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DYAD_MODULE = REPO_ROOT / ".agents" / "skills" / "shitsuji" / "scripts" / "dyad.py"


@pytest.fixture(scope="module")
def dyad():
    spec = importlib.util.spec_from_file_location("dyad_under_test", DYAD_MODULE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dyad_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestWheel:
    def test_wheel_has_8_emotions(self, dyad):
        assert len(dyad.WHEEL) == 8
        assert dyad.WHEEL[0] == "joy"
        assert dyad.WHEEL[-1] == "anticipation"

    def test_wheel_distance_self_is_zero(self, dyad):
        for emo in dyad.WHEEL:
            assert dyad.wheel_distance(emo, emo) == 0

    def test_wheel_distance_adjacent(self, dyad):
        assert dyad.wheel_distance("joy", "trust") == 1
        assert dyad.wheel_distance("anticipation", "joy") == 1

    def test_wheel_distance_antithesis(self, dyad):
        assert dyad.wheel_distance("joy", "sadness") == 4
        assert dyad.wheel_distance("trust", "disgust") == 4
        assert dyad.wheel_distance("fear", "anger") == 4
        assert dyad.wheel_distance("surprise", "anticipation") == 4

    def test_wheel_distance_unknown(self, dyad):
        assert dyad.wheel_distance("unknown", "joy") == -1
        assert dyad.wheel_distance("joy", "neutral") == -1


class TestDyadName:
    def test_primary_dyad_love(self, dyad):
        assert dyad.dyad_name("joy", "trust") == "love"
        assert dyad.dyad_name("trust", "joy") == "love"

    def test_secondary_dyad_hope(self, dyad):
        assert dyad.dyad_name("anticipation", "trust") == "hope"

    def test_tertiary_dyad_dominance(self, dyad):
        assert dyad.dyad_name("anger", "trust") == "dominance"

    def test_neutral_returns_none(self, dyad):
        assert dyad.dyad_name("neutral", "joy") is None
        assert dyad.dyad_name("joy", "neutral") is None

    def test_same_returns_none(self, dyad):
        assert dyad.dyad_name("joy", "joy") is None

    def test_antithesis_returns_none(self, dyad):
        assert dyad.dyad_name("joy", "sadness") is None
        assert dyad.dyad_name("trust", "disgust") is None
        assert dyad.dyad_name("fear", "anger") is None
        assert dyad.dyad_name("surprise", "anticipation") is None


class TestAntithesis:
    @pytest.mark.parametrize("pair", [
        ("joy", "sadness"),
        ("trust", "disgust"),
        ("fear", "anger"),
        ("surprise", "anticipation"),
    ])
    def test_antithesis_pairs(self, dyad, pair):
        a, b = pair
        assert dyad.is_antithesis(a, b) is True
        assert dyad.is_antithesis(b, a) is True

    def test_non_antithesis(self, dyad):
        assert dyad.is_antithesis("joy", "trust") is False
        assert dyad.is_antithesis("anger", "anticipation") is False

    def test_unknown_emotion(self, dyad):
        assert dyad.is_antithesis("joy", "unknown") is False


class TestFamily:
    def test_approach_family(self, dyad):
        for d in ("love", "optimism", "hope", "delight", "pride", "dominance"):
            assert dyad.dyad_family(d) == "approach"

    def test_withdraw_family(self, dyad):
        for d in ("submission", "awe", "disapproval", "remorse", "despair", "shame", "anxiety", "guilt"):
            assert dyad.dyad_family(d) == "withdraw"

    def test_aggressive_family(self, dyad):
        for d in ("contempt", "aggressiveness", "envy", "outrage", "cynicism"):
            assert dyad.dyad_family(d) == "aggressive"

    def test_ambivalent_family(self, dyad):
        for d in ("sentimentality", "morbidness", "unbelief", "curiosity", "pessimism"):
            assert dyad.dyad_family(d) == "ambivalent"

    def test_unknown_dyad(self, dyad):
        assert dyad.dyad_family("not-a-dyad") is None


class TestCoverage:
    def test_24_distinct_dyads(self, dyad):
        dyads = dyad.all_dyads()
        names = set(dyads.values())
        assert len(names) == 24

    def test_every_dyad_has_a_family(self, dyad):
        for name in dyad.all_dyads().values():
            assert dyad.dyad_family(name) is not None, f"{name} has no family"

    def test_no_antithesis_in_dyad_table(self, dyad):
        for (a, b), name in dyad.all_dyads().items():
            assert not dyad.is_antithesis(a, b), f"{name} maps to antithesis pair {a}+{b}"
