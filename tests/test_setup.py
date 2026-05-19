from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SETUP_SCRIPT = REPO_ROOT / "scripts" / "setup.py"


def load_setup_module():
    scripts_dir = str(SETUP_SCRIPT.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("setup_script", SETUP_SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_enables_hooks_feature_in_empty_config() -> None:
    setup = load_setup_module()

    assert setup.hooks_feature_state("") == "unset"
    assert setup.ensure_hooks_feature_enabled("") == "[features]\nhooks = true\n"


def test_updates_existing_hooks_feature_entry() -> None:
    setup = load_setup_module()

    config = "# keep me\n[features]\nhooks = false\nother = true\n"

    assert setup.hooks_feature_state(config) == "disabled"
    assert setup.ensure_hooks_feature_enabled(config) == (
        "# keep me\n[features]\nhooks = true\nother = true\n"
    )


def test_adds_features_section_without_dropping_existing_config() -> None:
    setup = load_setup_module()

    config = "[model]\ndefault = \"gpt-5\"\n"

    assert setup.ensure_hooks_feature_enabled(config) == (
        "[model]\ndefault = \"gpt-5\"\n\n[features]\nhooks = true\n"
    )


def test_detects_enabled_hooks_feature() -> None:
    setup = load_setup_module()

    config = "[features]\nhooks = true\n"

    assert setup.hooks_feature_state(config) == "enabled"


def test_replaces_deprecated_codex_hooks_entry() -> None:
    setup = load_setup_module()

    config = "# keep me\n[features]\ncodex_hooks = true\nother = true\n"

    assert setup.hooks_feature_state(config) == "deprecated"
    assert setup.ensure_hooks_feature_enabled(config) == (
        "# keep me\n[features]\nhooks = true\nother = true\n"
    )


def test_removes_deprecated_codex_hooks_when_hooks_exists() -> None:
    setup = load_setup_module()

    config = "[features]\nhooks = false\ncodex_hooks = true\n"

    assert setup.hooks_feature_state(config) == "disabled"
    assert setup.ensure_hooks_feature_enabled(config) == "[features]\nhooks = true\n"


def test_render_response_persona_section() -> None:
    setup = load_setup_module()

    assert setup.render_response_persona_section("明るく、根拠を出す。") == (
        "## Response persona\n\n明るく、根拠を出す。\n"
    )


def test_setup_dry_run_writes_nothing(tmp_path: Path) -> None:
    project = tmp_path / "project"
    codex_config = tmp_path / "config.toml"

    result = subprocess.run(
        [
            sys.executable,
            str(SETUP_SCRIPT),
            str(project),
            "--codex-config",
            str(codex_config),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert "---" in result.stdout
    assert "hooks = true" in result.stdout
    assert "SessionStart" in result.stdout
    assert not codex_config.exists()
    assert not (project / ".codex" / "hooks.json").exists()
    assert not (project / ".shitsuji" / "HISTORY.jsonl").exists()
    assert not (project / ".shitsuji" / "PERSONA_PROFILE.json").exists()
    assert not (project / "AGENTS.md").exists()


def test_generated_hooks_use_codex_matcher_shape(tmp_path: Path) -> None:
    setup = load_setup_module()

    data = setup.install_project_hooks(tmp_path / "project", REPO_ROOT)
    hooks = data["hooks"]

    assert hooks["SessionStart"][0]["matcher"] == "startup|resume"
    assert "matcher" not in hooks["UserPromptSubmit"][0]
    session_command = hooks["SessionStart"][0]["hooks"][0]["command"]
    assert "SHITSUJI_PROJECT_ROOT=" in session_command
    assert "SHITSUJI_CWD=" in session_command
    assert "SHITSUJI_DATA_DIR=" in session_command


def test_setup_prompts_before_adding_missing_codex_config(tmp_path: Path) -> None:
    project = tmp_path / "project"
    codex_config = tmp_path / "config.toml"

    result = subprocess.run(
        [
            sys.executable,
            str(SETUP_SCRIPT),
            str(project),
            "--codex-config",
            str(codex_config),
        ],
        input="n\n",
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert "Apply this change? [y/N]" in result.stderr
    assert "Skipped Codex config update" in result.stdout
    assert "Skipped response persona update" in result.stdout
    assert not codex_config.exists()
    assert (project / ".codex" / "hooks.json").exists()
    assert (project / ".shitsuji" / "HISTORY.jsonl").exists()
    assert (project / ".shitsuji" / "PERSONA_PROFILE.json").exists()
    assert not (project / "AGENTS.md").exists()


def test_setup_applies_prompted_codex_config_change(tmp_path: Path) -> None:
    project = tmp_path / "project"
    codex_config = tmp_path / "config.toml"

    result = subprocess.run(
        [
            sys.executable,
            str(SETUP_SCRIPT),
            str(project),
            "--codex-config",
            str(codex_config),
        ],
        input="y\n",
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert codex_config.read_text(encoding="utf-8") == (
        "[features]\nhooks = true\n"
    )
    assert (project / ".codex" / "hooks.json").exists()
    assert (project / ".shitsuji" / "HISTORY.jsonl").exists()
    assert (project / ".shitsuji" / "PERSONA_PROFILE.json").exists()


def test_setup_creates_agents_md_from_persona_prompt(tmp_path: Path) -> None:
    project = tmp_path / "project"

    result = subprocess.run(
        [
            sys.executable,
            str(SETUP_SCRIPT),
            str(project),
            "--skip-codex-config",
        ],
        input="オタクに優しいギャル。\n根拠と検証手順をちゃんと出す。\n\n",
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert (project / ".shitsuji" / "HISTORY.jsonl").exists()
    assert (project / ".shitsuji" / "PERSONA_PROFILE.json").exists()
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == (
        "## Response persona\n\n"
        "オタクに優しいギャル。\n"
        "根拠と検証手順をちゃんと出す。\n"
    )


def test_setup_appends_persona_to_existing_agents_md(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    agents = project / "AGENTS.md"
    agents.write_text("# Existing instructions\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SETUP_SCRIPT),
            str(project),
            "--skip-codex-config",
        ],
        input="明るめ、でも雑に褒めすぎない。\n\n",
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert (project / ".shitsuji" / "HISTORY.jsonl").exists()
    assert (project / ".shitsuji" / "PERSONA_PROFILE.json").exists()
    assert agents.read_text(encoding="utf-8") == (
        "# Existing instructions\n\n"
        "## Response persona\n\n"
        "明るめ、でも雑に褒めすぎない。\n"
    )
