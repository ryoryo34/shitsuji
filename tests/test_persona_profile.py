"""Unit tests for the PERSONA_PROFILE manager (.agents/skills/shitsuji/scripts/persona_profile.py).

Covers schema validation, atomic save, source-hash staleness detection,
and the volatility → halflife mapping that drives compute_mood.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_SCRIPT = REPO_ROOT / ".agents" / "skills" / "shitsuji" / "scripts" / "persona_profile.py"


def _load_module(data_dir: Path, source_dir: Path | None = None):
    """Load persona_profile with isolated DATA_DIR + source-file discovery.

    Re-imports so module-level path constants pick up the patched env vars.
    """
    os.environ["SHITSUJI_DATA_DIR"] = str(data_dir)
    if source_dir is not None:
        # Provide an explicit, isolated set of source files for the hash test.
        files = [str(p) for p in source_dir.glob("*") if p.is_file()]
        os.environ["SHITSUJI_PERSONA_SOURCES"] = ":".join(files)
    else:
        os.environ.pop("SHITSUJI_PERSONA_SOURCES", None)

    sys.modules.pop("persona_profile", None)
    spec = importlib.util.spec_from_file_location("persona_profile", PROFILE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["persona_profile"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def isolated_profile(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "AGENTS.md").write_text("Always respond in ギャル.\n")
    mod = _load_module(data_dir, source_dir)
    yield mod, data_dir, source_dir


# ---------------------------------------------------------------------------
# schema validation
# ---------------------------------------------------------------------------


class TestValidate:
    def test_accepts_valid_profile(self, isolated_profile):
        mod, _, _ = isolated_profile
        errors = mod.validate(
            {
                "name": "ギャル",
                "volatility": 0.85,
                "warmth": 0.6,
                "expressive_range": 0.9,
                "technical_rigor": 0.75,
                "style_profile": {
                    "tone": "warm and lively",
                    "distance": "close-but-respectful",
                    "formality": "casual",
                    "playfulness": "medium",
                    "explanation": "evidence-first",
                    "praise": "specific",
                    "challenge": "gentle",
                    "boundaries": ["do not overpraise"],
                },
                "rationale": "テスト",
            }
        )
        assert errors == []

    def test_rejects_volatility_out_of_range(self, isolated_profile):
        mod, _, _ = isolated_profile
        errors = mod.validate(
            {
                "name": "x",
                "volatility": 1.5,
                "warmth": 0.0,
                "expressive_range": 0.5,
                "rationale": "x",
            }
        )
        assert any("volatility" in e for e in errors)

    def test_rejects_warmth_out_of_range(self, isolated_profile):
        mod, _, _ = isolated_profile
        errors = mod.validate(
            {
                "name": "x",
                "volatility": 0.5,
                "warmth": -1.5,
                "expressive_range": 0.5,
                "rationale": "x",
            }
        )
        assert any("warmth" in e for e in errors)

    def test_rejects_missing_name(self, isolated_profile):
        mod, _, _ = isolated_profile
        errors = mod.validate(
            {
                "volatility": 0.5,
                "warmth": 0.0,
                "expressive_range": 0.5,
                "rationale": "x",
            }
        )
        assert any("name" in e for e in errors)

    def test_rejects_technical_rigor_out_of_range(self, isolated_profile):
        mod, _, _ = isolated_profile
        errors = mod.validate(
            {
                "name": "x",
                "volatility": 0.5,
                "warmth": 0.0,
                "expressive_range": 0.5,
                "technical_rigor": 2.0,
                "rationale": "x",
            }
        )
        assert any("technical_rigor" in e for e in errors)

    def test_rejects_invalid_style_profile(self, isolated_profile):
        mod, _, _ = isolated_profile
        errors = mod.validate(
            {
                "name": "x",
                "volatility": 0.5,
                "warmth": 0.0,
                "expressive_range": 0.5,
                "technical_rigor": 0.5,
                "style_profile": {"tone": 123, "boundaries": ["ok", 1]},
                "rationale": "x",
            }
        )
        assert any("style_profile.tone" in e for e in errors)
        assert any("style_profile.boundaries" in e for e in errors)


# ---------------------------------------------------------------------------
# read / write / persistence
# ---------------------------------------------------------------------------


class TestReadWrite:
    def test_read_returns_none_when_missing(self, isolated_profile):
        mod, _, _ = isolated_profile
        assert mod.read_profile() is None

    def test_write_then_read_roundtrip(self, isolated_profile):
        mod, _, _ = isolated_profile
        payload = {
            "name": "ギャル",
            "volatility": 0.85,
            "warmth": 0.6,
            "expressive_range": 0.9,
            "rationale": "テスト",
        }
        mod.write_profile(payload)
        loaded = mod.read_profile()
        assert loaded["name"] == "ギャル"
        assert loaded["volatility"] == 0.85
        assert loaded["technical_rigor"] == mod.DEFAULT_TECHNICAL_RIGOR
        assert loaded["style_profile"]["explanation"] == "balanced"
        assert loaded["source_kind"] == "codex_instruction_chain"
        assert "ts" in loaded
        assert "source_hash" in loaded
        assert "source_files" in loaded

    def test_write_rejects_invalid_schema(self, isolated_profile):
        mod, _, _ = isolated_profile
        with pytest.raises(SystemExit) as exc:
            mod.write_profile({"name": "x", "volatility": 99.0, "warmth": 0, "expressive_range": 0.5, "rationale": "x"})
        assert exc.value.code == 2


class TestAutomaticDerivation:
    def test_derives_gal_persona_from_response_persona_section(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        source_dir = tmp_path / "sources"
        source_dir.mkdir()
        (source_dir / "AGENTS.md").write_text(
            "# Project\n\n"
            "general instructions\n\n"
            "## Response persona\n\n"
            "オタクに優しいギャル。技術判断は根拠と検証手順をちゃんと出す。\n",
            encoding="utf-8",
        )
        mod = _load_module(data_dir, source_dir)
        profile = mod.ensure_profile_current()
        assert profile["name"] == "ギャル"
        assert profile["volatility"] >= 0.8
        assert profile["warmth"] >= 0.6
        assert profile["technical_rigor"] >= 0.78
        assert (data_dir / "PERSONA_PROFILE.json").exists()

    def test_derives_default_profile_when_no_sources(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        empty_sources = tmp_path / "sources"
        empty_sources.mkdir()
        mod = _load_module(data_dir, empty_sources)
        profile = mod.ensure_profile_current()
        assert profile["name"] == "default"
        assert profile["volatility"] == mod.DEFAULT_VOLATILITY


# ---------------------------------------------------------------------------
# staleness detection
# ---------------------------------------------------------------------------


class TestStaleness:
    def test_missing_profile_is_stale(self, isolated_profile):
        mod, _, _ = isolated_profile
        assert mod.is_stale() is True

    def test_fresh_profile_is_not_stale(self, isolated_profile):
        mod, _, _ = isolated_profile
        mod.write_profile({"name": "x", "volatility": 0.5, "warmth": 0, "expressive_range": 0.5, "rationale": "x"})
        assert mod.is_stale() is False

    def test_source_change_marks_stale(self, isolated_profile, tmp_path):
        mod, data_dir, source_dir = isolated_profile
        mod.write_profile({"name": "x", "volatility": 0.5, "warmth": 0, "expressive_range": 0.5, "rationale": "x"})
        assert mod.is_stale() is False
        # Mutate the source file and reload the module so the new hash is computed.
        (source_dir / "AGENTS.md").write_text("Always respond in formal counselor.\n")
        mod = _load_module(data_dir, source_dir)
        assert mod.is_stale() is True

    def test_codex_agents_chain_prefers_override_and_nested(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        codex_home = tmp_path / "codex-home"
        codex_home.mkdir()
        project = tmp_path / "project"
        nested = project / "pkg"
        nested.mkdir(parents=True)
        (project / ".git").mkdir()
        (codex_home / "AGENTS.md").write_text("global regular\n")
        (codex_home / "AGENTS.override.md").write_text("global override\n")
        (project / "AGENTS.md").write_text("project regular\n")
        (nested / "AGENTS.override.md").write_text("nested override\n")
        monkeypatch.setenv("CODEX_HOME", str(codex_home))
        monkeypatch.setenv("SHITSUJI_CWD", str(nested))
        monkeypatch.setenv("SHITSUJI_PROJECT_ROOT", str(project))
        mod = _load_module(data_dir)
        files = [p.name for p in mod.discover_source_files()]
        paths = [str(p) for p in mod.discover_source_files()]
        assert files == ["AGENTS.override.md", "AGENTS.md", "AGENTS.override.md"]
        assert str(codex_home / "AGENTS.override.md") in paths
        assert str(project / "AGENTS.md") in paths
        assert str(nested / "AGENTS.override.md") in paths

    def test_project_root_env_is_used_when_cwd_is_outside_project(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (project / "AGENTS.md").write_text("project persona\n")
        monkeypatch.setenv("SHITSUJI_PROJECT_ROOT", str(project))
        monkeypatch.setenv("SHITSUJI_CWD", str(outside))
        mod = _load_module(data_dir)
        assert project / "AGENTS.md" in mod.discover_source_files()


# ---------------------------------------------------------------------------
# halflife mapping
# ---------------------------------------------------------------------------


class TestHalflife:
    @pytest.mark.parametrize(
        "volatility, expected",
        [
            (0.0, 12),
            (0.5, 7),
            (1.0, 2),
        ],
    )
    def test_halflife_for_volatility_anchors(self, isolated_profile, volatility, expected):
        mod, _, _ = isolated_profile
        assert mod.halflife_for_volatility(volatility) == expected

    def test_halflife_monotonic_decreasing(self, isolated_profile):
        mod, _, _ = isolated_profile
        prev = None
        for v in [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0]:
            cur = mod.halflife_for_volatility(v)
            if prev is not None:
                assert cur <= prev
            prev = cur

    def test_recommended_halflife_falls_back_when_no_profile(self, isolated_profile):
        mod, _, _ = isolated_profile
        assert mod.recommended_halflife() == mod.DEFAULT_HALFLIFE  # 5

    def test_recommended_halflife_uses_profile_when_present(self, isolated_profile):
        mod, _, _ = isolated_profile
        mod.write_profile({"name": "x", "volatility": 0.85, "warmth": 0, "expressive_range": 0.9, "rationale": "x"})
        # 0.85 → 2 + 10 * 0.15 = 3.5 → round → 4
        assert mod.recommended_halflife() == 4


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


class TestCLI:
    def test_show_prints_empty_when_missing(self, tmp_path):
        import subprocess
        env = os.environ.copy()
        env["SHITSUJI_DATA_DIR"] = str(tmp_path)
        proc = subprocess.run([str(PROFILE_SCRIPT), "--show"], capture_output=True, text=True, env=env)
        assert proc.returncode == 0
        assert json.loads(proc.stdout) == {}

    def test_check_stale_exits_2_when_missing(self, tmp_path):
        import subprocess
        env = os.environ.copy()
        env["SHITSUJI_DATA_DIR"] = str(tmp_path)
        proc = subprocess.run([str(PROFILE_SCRIPT), "--check-stale"], capture_output=True, text=True, env=env)
        assert proc.returncode == 2

    def test_halflife_outputs_int(self, tmp_path):
        import subprocess
        env = os.environ.copy()
        env["SHITSUJI_DATA_DIR"] = str(tmp_path)
        proc = subprocess.run([str(PROFILE_SCRIPT), "--halflife"], capture_output=True, text=True, env=env)
        assert proc.returncode == 0
        assert int(proc.stdout.strip()) >= 2
