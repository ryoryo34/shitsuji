#!/usr/bin/env python3
"""One-command local setup for Shitsuji.

Usage:
    python3 scripts/setup.py /path/to/project

This enables Hooks in the user's Codex config and installs the
project-local Shitsuji hook entries.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

RUNTIME_DIR_NAME = ".shitsuji"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enable Codex Hooks and install Shitsuji into a project."
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Project directory where Codex runs. Defaults to the current directory.",
    )
    parser.add_argument(
        "--codex-config",
        default=str(Path.home() / ".codex" / "config.toml"),
        help="Path to Codex config.toml. Defaults to ~/.codex/config.toml.",
    )
    parser.add_argument(
        "--skip-codex-config",
        action="store_true",
        help="Install project hooks without editing Codex config.toml.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the files that would be written instead of writing them.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Apply Codex config changes without prompting.",
    )
    return parser.parse_args()


def is_shitsuji_hook(hook: Any) -> bool:
    if not isinstance(hook, dict):
        return False
    command = hook.get("command")
    if not isinstance(command, str):
        return False
    return "SHITSUJI_" in command or ".agents/hooks/shitsuji/" in command


def remove_existing_shitsuji_entries(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        return []

    kept: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            kept.append(entry)
            continue

        remaining_hooks = [hook for hook in hooks if not is_shitsuji_hook(hook)]
        if not remaining_hooks:
            continue

        new_entry = dict(entry)
        new_entry["hooks"] = remaining_hooks
        kept.append(new_entry)

    return kept


def command_with_env(env: dict[str, Path], script: Path) -> str:
    prefix = " ".join(f'{name}="{value}"' for name, value in env.items())
    return f'{prefix} python3 "{script}"'


def generated_entries(project_root: Path, shitsuji_root: Path) -> dict[str, dict[str, Any]]:
    hooks_dir = shitsuji_root / ".agents" / "hooks" / "shitsuji"
    session_start = hooks_dir / "session_start.py"
    user_prompt_submit = hooks_dir / "codex_project_user_prompt_submit.py"

    missing = [path for path in (session_start, user_prompt_submit) if not path.exists()]
    if missing:
        paths = "\n".join(f"  - {path}" for path in missing)
        raise SystemExit(f"Missing Shitsuji hook script(s):\n{paths}")

    return {
        "SessionStart": {
            "matcher": "startup|resume",
            "hooks": [
                {
                    "type": "command",
                    "command": command_with_env(
                        {
                            "SHITSUJI_PROJECT_ROOT": project_root,
                            "SHITSUJI_CWD": project_root,
                            "SHITSUJI_DATA_DIR": project_root / RUNTIME_DIR_NAME,
                        },
                        session_start,
                    ),
                    "timeout": 30,
                }
            ],
        },
        "UserPromptSubmit": {
            "hooks": [
                {
                    "type": "command",
                    "command": command_with_env(
                        {"SHITSUJI_PROJECT_ROOT": project_root},
                        user_prompt_submit,
                    ),
                    "timeout": 10,
                }
            ],
        },
    }


def load_existing_hooks(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"hooks": {}}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"Cannot parse existing hooks file: {path}\n{e}") from e

    if not isinstance(data, dict):
        raise SystemExit(f"Existing hooks file must contain a JSON object: {path}")

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SystemExit(f"Existing hooks file has non-object 'hooks': {path}")

    return data


def install_project_hooks(project_root: Path, shitsuji_root: Path) -> dict[str, Any]:
    hooks_file = project_root / ".codex" / "hooks.json"
    data = load_existing_hooks(hooks_file)
    hooks = data["hooks"]
    entries_by_event = generated_entries(project_root, shitsuji_root)

    for event, entry in entries_by_event.items():
        existing_entries = remove_existing_shitsuji_entries(hooks.get(event))
        existing_entries.append(entry)
        hooks[event] = existing_entries

    return data


def find_features_section(lines: list[str]) -> tuple[int | None, int]:
    features_start: int | None = None
    features_end = len(lines)

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[features]":
            features_start = index
            continue
        if features_start is not None and index > features_start:
            if stripped.startswith("[") and stripped.endswith("]"):
                features_end = index
                break

    return features_start, features_end


def feature_value(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    return key.strip(), value.strip().lower()


def hooks_feature_state(content: str) -> str:
    """Return enabled, disabled, unset, or deprecated for [features].hooks."""
    lines = content.splitlines()
    features_start, features_end = find_features_section(lines)
    if features_start is None:
        return "unset"

    legacy_seen = False
    for index in range(features_start + 1, features_end):
        feature = feature_value(lines[index])
        if feature is None:
            continue
        key, value = feature
        if key == "hooks":
            if value == "true":
                return "deprecated" if legacy_seen else "enabled"
            return "disabled"
        if key == "codex_hooks":
            legacy_seen = True

    if legacy_seen:
        return "deprecated"

    return "unset"


def ensure_hooks_feature_enabled(content: str) -> str:
    """Return config.toml content with [features].hooks set to true.

    This keeps the existing file as text instead of round-tripping through a
    TOML writer, so comments and unrelated formatting survive.
    """
    lines = content.splitlines()
    if not lines:
        return "[features]\nhooks = true\n"

    features_start, features_end = find_features_section(lines)

    if features_start is None:
        prefix = "\n" if content.endswith("\n") else "\n\n"
        return content + prefix + "[features]\nhooks = true\n"

    hooks_index: int | None = None
    legacy_indexes: set[int] = set()
    for index in range(features_start + 1, features_end):
        feature = feature_value(lines[index])
        if feature is None:
            continue
        key, _value = feature
        if key == "hooks":
            hooks_index = index
        elif key == "codex_hooks":
            legacy_indexes.add(index)

    if hooks_index is not None:
        indent = lines[hooks_index][: len(lines[hooks_index]) - len(lines[hooks_index].lstrip())]
        lines[hooks_index] = f"{indent}hooks = true"
        lines = [line for index, line in enumerate(lines) if index not in legacy_indexes]
        return "\n".join(lines) + "\n"

    if legacy_indexes:
        first_legacy = min(legacy_indexes)
        indent = lines[first_legacy][: len(lines[first_legacy]) - len(lines[first_legacy].lstrip())]
        lines[first_legacy] = f"{indent}hooks = true"
        legacy_indexes.remove(first_legacy)
        lines = [line for index, line in enumerate(lines) if index not in legacy_indexes]
        return "\n".join(lines) + "\n"

    lines.insert(features_end, "hooks = true")
    return "\n".join(lines) + "\n"


def render_codex_config_change(
    *,
    path: Path,
    current_state: str,
    new_content: str,
) -> str:
    if current_state == "unset":
        reason = "hooks is not set"
    elif current_state == "deprecated":
        reason = "codex_hooks is deprecated"
    else:
        reason = "hooks is not enabled"

    return "\n".join(
        [
            f"Codex Hooks need to be enabled in {path}.",
            f"Current state: {current_state} ({reason}).",
            "Proposed config.toml content:",
            "",
            new_content.rstrip(),
            "",
        ]
    )


def confirm_codex_config_change(
    *,
    path: Path,
    current_state: str,
    new_content: str,
) -> bool:
    sys.stderr.write(
        render_codex_config_change(
            path=path,
            current_state=current_state,
            new_content=new_content,
        )
    )
    sys.stderr.write("Apply this change? [y/N] ")
    sys.stderr.flush()
    answer = sys.stdin.readline().strip().lower()
    return answer in {"y", "yes"}


def render_response_persona_section(persona: str) -> str:
    return f"## Response persona\n\n{persona.strip()}\n"


def prompt_response_persona(agents_file: Path) -> str:
    action = "append to" if agents_file.exists() else "create"
    sys.stderr.write(
        "\n"
        f"Enter a freeform response persona to {action} {agents_file}.\n"
        "This is the writing-style policy Shitsuji uses for tone calibration.\n"
        "Finish with an empty line. Leave empty to skip.\n"
        "> "
    )
    sys.stderr.flush()

    lines: list[str] = []
    while True:
        line = sys.stdin.readline()
        if line == "":
            break
        if line in {"\n", "\r\n"}:
            break
        lines.append(line.rstrip("\r\n"))
        sys.stderr.write("> ")
        sys.stderr.flush()

    return "\n".join(lines).strip()


def write_response_persona(agents_file: Path, persona: str) -> bool:
    if not persona.strip():
        return False

    section = render_response_persona_section(persona)
    if not agents_file.exists():
        agents_file.write_text(section, encoding="utf-8")
        return True

    current = agents_file.read_text(encoding="utf-8")
    if not current or current.endswith("\n\n"):
        separator = ""
    elif current.endswith("\n"):
        separator = "\n"
    else:
        separator = "\n\n"
    agents_file.write_text(current + separator + section, encoding="utf-8")
    return True


def initialize_runtime_files(project_root: Path) -> tuple[Path, Path]:
    runtime_dir = project_root / RUNTIME_DIR_NAME
    history_file = runtime_dir / "HISTORY.jsonl"
    persona_profile_file = runtime_dir / "PERSONA_PROFILE.json"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    history_file.touch(exist_ok=True)
    persona_profile_file.touch(exist_ok=True)
    return history_file, persona_profile_file


def render_dry_run(
    *,
    codex_config_path: Path,
    codex_config: str | None,
    hooks_file: Path,
    hooks_data: dict,
) -> str:
    output: list[str] = []
    if codex_config is not None:
        output.extend(
            [
                f"--- {codex_config_path}",
                codex_config.rstrip(),
                "",
            ]
        )
    output.extend(
        [
            f"--- {hooks_file}",
            json.dumps(hooks_data, ensure_ascii=False, indent=2),
            "",
        ]
    )
    return "\n".join(output)


def main() -> int:
    args = parse_args()
    script_path = Path(__file__).resolve()
    shitsuji_root = script_path.parents[1]
    project_root = Path(args.project).expanduser().resolve()
    hooks_file = project_root / ".codex" / "hooks.json"
    agents_file = project_root / "AGENTS.md"
    codex_config_path = Path(args.codex_config).expanduser().resolve()

    codex_config: str | None = None
    codex_config_state = "skipped"
    if not args.skip_codex_config:
        current_config = ""
        if codex_config_path.exists():
            current_config = codex_config_path.read_text(encoding="utf-8")
        codex_config_state = hooks_feature_state(current_config)
        codex_config = ensure_hooks_feature_enabled(current_config)

    hooks_data = install_project_hooks(project_root, shitsuji_root)

    if args.dry_run:
        sys.stdout.write(
            render_dry_run(
                codex_config_path=codex_config_path,
                codex_config=codex_config,
                hooks_file=hooks_file,
                hooks_data=hooks_data,
            )
        )
        return 0

    if codex_config is not None:
        should_write_config = True
        if codex_config_state != "enabled" and not args.yes:
            should_write_config = confirm_codex_config_change(
                path=codex_config_path,
                current_state=codex_config_state,
                new_content=codex_config,
            )

        if should_write_config:
            codex_config_path.parent.mkdir(parents=True, exist_ok=True)
            codex_config_path.write_text(codex_config, encoding="utf-8")
        else:
            codex_config = None
            print(f"Skipped Codex config update: {codex_config_path}")

    rendered_hooks = json.dumps(hooks_data, ensure_ascii=False, indent=2) + "\n"
    hooks_file.parent.mkdir(parents=True, exist_ok=True)
    hooks_file.write_text(rendered_hooks, encoding="utf-8")
    history_file, persona_profile_file = initialize_runtime_files(project_root)

    if codex_config is not None:
        print(f"Enabled Codex Hooks: {codex_config_path}")
    print(f"Installed Shitsuji hooks: {hooks_file}")
    print(f"Initialized Shitsuji history: {history_file}")
    print(f"Initialized Shitsuji persona profile: {persona_profile_file}")
    persona = prompt_response_persona(agents_file)
    if write_response_persona(agents_file, persona):
        print(f"Updated response persona: {agents_file}")
    else:
        print(f"Skipped response persona update: {agents_file}")
    print(f"Project root: {project_root}")
    print(f"Shitsuji root: {shitsuji_root}")
    print("Restart Codex in the project to load the new hook configuration.")
    print(
        "If Codex says hooks need review, open /hooks and trust the project "
        "SessionStart and UserPromptSubmit hooks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
