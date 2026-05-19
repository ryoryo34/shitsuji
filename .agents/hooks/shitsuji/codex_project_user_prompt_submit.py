#!/usr/bin/env python3
"""Project-scoped wrapper for Codex UserPromptSubmit.

Codex hook registration is global in many setups. This wrapper makes that
safe by checking the hook payload's ``cwd`` against ``SHITSUJI_PROJECT_ROOT``.
Outside that project it emits a no-op hook envelope; inside it delegates to
``user_prompt_submit.py`` with a project-local default data directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK_EVENT = "UserPromptSubmit"
THIS_DIR = Path(__file__).resolve().parent
INNER_HOOK = THIS_DIR / "user_prompt_submit.py"
RUNTIME_DIR_NAME = ".shitsuji"


def emit_noop() -> None:
    json.dump({"hookSpecificOutput": {"hookEventName": HOOK_EVENT}}, sys.stdout)
    sys.stdout.write("\n")


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
    except OSError:
        return False


def main() -> None:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        emit_noop()
        return

    project_root_raw = os.environ.get("SHITSUJI_PROJECT_ROOT")
    cwd_raw = payload.get("cwd") or os.getcwd()
    if not project_root_raw:
        emit_noop()
        return

    project_root = Path(project_root_raw)
    cwd = Path(cwd_raw)
    if not is_inside(cwd, project_root):
        emit_noop()
        return

    env = os.environ.copy()
    env["SHITSUJI_DATA_DIR"] = str(project_root / RUNTIME_DIR_NAME)
    proc = subprocess.run(
        [sys.executable, str(INNER_HOOK)],
        input=raw,
        capture_output=True,
        text=True,
        env=env,
        timeout=12,
    )
    sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
