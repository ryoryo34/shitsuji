#!/usr/bin/env python3
"""Refresh a Shitsuji HISTORY.jsonl with the current auto-memory heuristic.

The hook stores lightweight auto-memory entries whose rationale contains a
short `text='...'` excerpt. This script extracts that excerpt, re-runs the
current `estimate_user_turn()` implementation, preserves the original `ts`,
and rewrites the JSONL in the current runtime schema.

By default this is a dry run. Use `--in-place` to rewrite the file. A `.bak`
copy is created unless `--no-backup` is passed.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_FILE = REPO_ROOT / ".agents" / "hooks" / "shitsuji" / "user_prompt_submit.py"
APPEND_FILE = REPO_ROOT / ".agents" / "skills" / "shitsuji" / "scripts" / "append.py"
DEFAULT_HISTORY = REPO_ROOT / ".codex" / "shitsuji" / "HISTORY.jsonl"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"failed to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def extract_prompt(entry: dict) -> str | None:
    ue = entry.get("user_emotion")
    if not isinstance(ue, dict):
        return None
    rationale = ue.get("rationale")
    if not isinstance(rationale, str):
        return None
    marker = "text="
    idx = rationale.find(marker)
    if idx < 0:
        return None
    raw = rationale[idx + len(marker):].strip()
    try:
        parsed = ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        return None
    return parsed if isinstance(parsed, str) else None


def read_entries(path: Path) -> tuple[list[dict], int]:
    entries: list[dict] = []
    bad_json = 0
    if not path.exists():
        return entries, bad_json
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            bad_json += 1
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    return entries, bad_json


def summarize(entries: list[dict]) -> str:
    labels: dict[str, int] = {}
    points: list[tuple[float, float, float, float]] = []
    for entry in entries:
        ue = entry.get("user_emotion")
        if not isinstance(ue, dict):
            continue
        try:
            vad = tuple(float(ue.get(axis, 0.0)) for axis in ("valence", "arousal", "dominance"))
            confidence = float(entry.get("confidence", 0.7))
        except (TypeError, ValueError):
            continue
        points.append((*vad, confidence))
        label = str(ue.get("primary") or "unknown")
        labels[label] = labels.get(label, 0) + 1
    if not points:
        return f"entries={len(entries)} user_points=0"
    n = len(points)
    means = tuple(sum(point[i] for point in points) / n for i in range(3))
    conf = sum(point[3] for point in points) / n
    label_text = ",".join(f"{k}:{v}" for k, v in sorted(labels.items()))
    return (
        f"entries={len(entries)} user_points={n} "
        f"mean_vad=({means[0]:+.2f},{means[1]:+.2f},{means[2]:+.2f}) "
        f"mean_conf={conf:.2f} labels={label_text}"
    )


def refresh_entries(entries: list[dict]) -> tuple[list[dict], dict[str, int]]:
    hook = load_module("shitsuji_user_prompt_submit_for_refresh", HOOK_FILE)
    append = load_module("shitsuji_append_for_refresh", APPEND_FILE)
    refreshed: list[dict] = []
    stats = {"refreshed": 0, "kept_without_prompt_text": 0}
    for entry in entries:
        prompt = extract_prompt(entry)
        if prompt is None:
            refreshed.append(entry)
            stats["kept_without_prompt_text"] += 1
            continue
        event = hook.estimate_user_turn(prompt)
        event = append.annotate_dyad(event)
        if "ts" in entry:
            event = {"ts": entry["ts"], **event}
        refreshed.append(event)
        stats["refreshed"] += 1
    return refreshed, stats


def write_jsonl(path: Path, entries: list[dict]) -> None:
    text = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args(argv)

    history = args.history.resolve()
    entries, bad_json = read_entries(history)
    refreshed, stats = refresh_entries(entries)
    print(f"history: {history}")
    print(f"bad_json: {bad_json}")
    print(f"before: {summarize(entries)}")
    print(f"after:  {summarize(refreshed)}")
    print(f"stats:  {stats}")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_jsonl(args.output, refreshed)
        print(f"wrote: {args.output}")
        return 0

    if args.in_place:
        if not args.no_backup and history.exists():
            backup = history.with_suffix(history.suffix + ".bak")
            shutil.copy2(history, backup)
            print(f"backup: {backup}")
        write_jsonl(history, refreshed)
        print("updated in place")
    else:
        print("dry-run only; pass --in-place to rewrite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
