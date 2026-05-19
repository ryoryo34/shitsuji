#!/usr/bin/env python3
"""Manager for SHITSUJI_DATA_DIR/PERSONA_PROFILE.json — the writing-style snapshot.

The shitsuji Skill consumes a single source of persona truth: the
**writing-style persona** the user has configured through Codex instruction
sources such as `AGENTS.md` and `~/.codex/instructions.md`. The profile is
derived deterministically inside the hook process so SessionStart does not
need to inject a long derivation rubric into Codex context.

Schema:
    {
      "ts": "<ISO-8601 UTC>",
      "name": "<short label, e.g. 'ギャル'>",
      "volatility":       <float in [0.0, 1.0]>,  // 0=stoic / 1=theatrical
      "warmth":           <float in [-1.0, 1.0]>, // resting valence
      "expressive_range": <float in [0.0, 1.0]>,  // 0=reserved / 1=open
      "technical_rigor":  <float in [0.0, 1.0]>,  // 0=casual / 1=evidence-first
      "style_profile": {
        "tone": "<short phrase>",
        "distance": "<short phrase>",
        "formality": "<short phrase>",
        "playfulness": "<short phrase>",
        "explanation": "<short phrase>",
        "praise": "<short phrase>",
        "challenge": "<short phrase>",
        "boundaries": ["<rule>", ...]
      },
      "rationale":        "<short Japanese sentence>",
      "source_kind":      "codex_instruction_chain",
      "source_hash":      "<sha256 hex of source-file contents>",
      "source_files":     ["<path>", ...]
    }

The hash makes staleness detection deterministic: if the underlying
writing-style files change, `is_stale()` returns True and SessionStart
refreshes the derived cache.

CLI:
    persona_profile.py --show          # print current profile JSON
    persona_profile.py --source-hash   # print just the source hash
    persona_profile.py --check-stale   # exit 0 fresh, 1 stale, 2 missing
    persona_profile.py --halflife      # print recommended halflife (int)
    persona_profile.py --ensure        # create/refresh derived profile
    persona_profile.py --clear         # remove the profile file
    persona_profile.py < new.json      # save (validates schema)
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SHITSUJI_DATA_DIR") or (SKILL_DIR / "data"))
PROFILE_FILE = DATA_DIR / "PERSONA_PROFILE.json"

DEFAULT_VOLATILITY = 0.5
DEFAULT_WARMTH = 0.0
DEFAULT_EXPRESSIVE_RANGE = 0.5
DEFAULT_TECHNICAL_RIGOR = 0.5
DEFAULT_HALFLIFE = 5
HALFLIFE_MIN = 2
HALFLIFE_MAX = 12


# ---------------------------------------------------------------------------
# source discovery + hashing
# ---------------------------------------------------------------------------


def discover_source_files() -> list[Path]:
    """Return the writing-style persona source files that exist.

    Override with SHITSUJI_PERSONA_SOURCES (colon-separated paths) for
    tests or non-standard layouts.

    The default search mirrors Codex's AGENTS.md instruction chain:
    a global file under CODEX_HOME (or ~/.codex), followed by one
    instruction file per directory from project root to cwd. In each
    directory AGENTS.override.md wins over AGENTS.md. Optional fallback
    names can be supplied through SHITSUJI_PROJECT_DOC_FALLBACK_FILENAMES
    as a colon-separated list; this keeps the runtime stdlib-only while
    allowing tests or users to match their Codex config.
    """
    override = os.environ.get("SHITSUJI_PERSONA_SOURCES")
    if override:
        return [Path(p).expanduser() for p in override.split(":") if Path(p).expanduser().exists()]

    fallback_names = [
        name
        for name in os.environ.get("SHITSUJI_PROJECT_DOC_FALLBACK_FILENAMES", "").split(":")
        if name
    ]

    files: list[Path] = []

    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    global_file = _first_existing(
        codex_home,
        ["AGENTS.override.md", "AGENTS.md", *fallback_names],
    )
    if global_file is not None:
        files.append(global_file)

    cwd = Path(os.environ.get("SHITSUJI_CWD") or os.getcwd()).resolve()
    root = _project_root(cwd)
    try:
        cwd.relative_to(root)
    except ValueError:
        cwd = root
    for directory in _path_chain(root, cwd):
        local_file = _first_existing(
            directory,
            ["AGENTS.override.md", "AGENTS.md", *fallback_names],
        )
        if local_file is not None:
            files.append(local_file)
    return files


def _first_existing(directory: Path, names: list[str]) -> Path | None:
    for name in names:
        path = directory / name
        if path.exists() and path.is_file():
            return path
    return None


def _project_root(cwd: Path) -> Path:
    env_root = os.environ.get("SHITSUJI_PROJECT_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    cur = cwd
    while True:
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return cwd
        cur = cur.parent


def _path_chain(root: Path, cwd: Path) -> list[Path]:
    try:
        rel = cwd.relative_to(root)
    except ValueError:
        return [cwd]
    chain = [root]
    cur = root
    for part in rel.parts:
        cur = cur / part
        chain.append(cur)
    return chain


def compute_source_hash(paths: list[Path] | None = None) -> str:
    """Stable SHA256 over the concatenated source file contents.

    Order matters; we sort by path string to keep the hash stable across
    invocations regardless of discovery order.
    """
    paths = paths if paths is not None else discover_source_files()
    h = hashlib.sha256()
    for p in sorted(paths, key=str):
        try:
            h.update(p.read_bytes())
            h.update(b"\x00")
        except OSError:
            continue
    return h.hexdigest()


def read_source_text(paths: list[Path] | None = None) -> str:
    """Return concatenated persona source text, preferring Response persona.

    A `## Response persona` section is the strongest signal because setup.py
    writes exactly that section. If no such section exists, use the full
    instruction text as a conservative fallback.
    """
    paths = paths if paths is not None else discover_source_files()
    sections: list[str] = []
    full_texts: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        full_texts.append(text)
        extracted = _extract_response_persona_section(text)
        if extracted.strip():
            sections.append(extracted.strip())
    return "\n\n".join(sections or full_texts)


def _extract_response_persona_section(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip().lower()
            if title == "response persona":
                in_section = True
                continue
            if in_section:
                break
        elif in_section:
            out.append(line)
    return "\n".join(out)


def derive_profile_from_sources() -> dict:
    """Derive a conservative persona profile from instruction source text."""
    text = read_source_text()
    lowered = text.lower()
    if not text.strip():
        return {
            "name": "default",
            "volatility": DEFAULT_VOLATILITY,
            "warmth": DEFAULT_WARMTH,
            "expressive_range": DEFAULT_EXPRESSIVE_RANGE,
            "technical_rigor": DEFAULT_TECHNICAL_RIGOR,
            "style_profile": default_style_profile(),
            "rationale": "persona指定が見つからないため、保守的なdefault設定を使います。",
        }

    volatility = DEFAULT_VOLATILITY
    warmth = DEFAULT_WARMTH
    expressive = DEFAULT_EXPRESSIVE_RANGE
    rigor = DEFAULT_TECHNICAL_RIGOR
    name = "custom"
    cues: list[str] = []

    def has(*words: str) -> bool:
        return any(word in text or word in lowered for word in words)

    if has("オタクに優しいギャル", "ギャル"):
        name = "ギャル"
        volatility = max(volatility, 0.82)
        warmth = max(warmth, 0.62)
        expressive = max(expressive, 0.88)
        cues.append("ギャル/明るい距離感")
    if has("テンション高", "感情豊か", "lively", "energetic", "playful"):
        volatility = max(volatility, 0.75)
        expressive = max(expressive, 0.78)
        cues.append("高めの表現")
    if has("warm friend", "親しみ", "優しい", "やさしい", "寄り添", "close"):
        warmth = max(warmth, 0.48)
        expressive = max(expressive, 0.60)
        if name == "custom":
            name = "warm"
        cues.append("温かい応答")
    if has("stoic", "冷静", "事実ベース", "淡々", "formal", "professional", "counselor"):
        volatility = min(volatility, 0.30)
        expressive = min(expressive, 0.42)
        warmth = max(warmth, 0.15) if has("counselor", "寄り添") else warmth
        if name == "custom":
            name = "stoic"
        cues.append("冷静/専門的")
    if has("辛口", "批評", "critic", "ツッコミ", "challenge"):
        volatility = max(volatility, 0.70)
        expressive = max(expressive, 0.75)
        warmth = min(max(warmth, -0.05), 0.35)
        if name == "custom":
            name = "critic"
        cues.append("批評/ツッコミ")
    if has("根拠", "検証", "evidence", "verify", "verification", "technical", "技術判断", "正確"):
        rigor = max(rigor, 0.78)
        cues.append("根拠と検証")
    if has("カジュアル", "casual", "フランク"):
        expressive = max(expressive, 0.62)
        cues.append("カジュアル")
    if has("褒めすぎ", "overpraise", "雑に褒め"):
        rigor = max(rigor, 0.70)
        cues.append("過剰な称賛を避ける")

    payload = {
        "name": name,
        "volatility": round(max(0.0, min(1.0, volatility)), 2),
        "warmth": round(max(-1.0, min(1.0, warmth)), 2),
        "expressive_range": round(max(0.0, min(1.0, expressive)), 2),
        "technical_rigor": round(max(0.0, min(1.0, rigor)), 2),
    }
    payload["style_profile"] = default_style_profile(payload)
    cue_text = "、".join(cues[:4]) if cues else "自由記述persona"
    payload["rationale"] = f"AGENTS系の記述から「{cue_text}」を検出し、保守的に自動推定しました。"
    return payload


# ---------------------------------------------------------------------------
# read / write / validate
# ---------------------------------------------------------------------------


def _is_in(value, lo: float, hi: float) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return lo <= v <= hi


def validate(obj: dict) -> list[str]:
    """Return list of human-readable schema violations; empty = valid."""
    errors: list[str] = []
    if not _is_in(obj.get("volatility"), 0.0, 1.0):
        errors.append(f"volatility must be float in [0.0, 1.0], got {obj.get('volatility')!r}")
    if not _is_in(obj.get("warmth"), -1.0, 1.0):
        errors.append(f"warmth must be float in [-1.0, 1.0], got {obj.get('warmth')!r}")
    if not _is_in(obj.get("expressive_range"), 0.0, 1.0):
        errors.append(f"expressive_range must be float in [0.0, 1.0], got {obj.get('expressive_range')!r}")
    if "technical_rigor" in obj and not _is_in(obj.get("technical_rigor"), 0.0, 1.0):
        errors.append(f"technical_rigor must be float in [0.0, 1.0], got {obj.get('technical_rigor')!r}")
    if not isinstance(obj.get("name"), str):
        errors.append("name must be a string")
    if not isinstance(obj.get("rationale"), str):
        errors.append("rationale must be a string")
    style = obj.get("style_profile")
    if style is not None:
        if not isinstance(style, dict):
            errors.append("style_profile must be an object when present")
        else:
            for key in ("tone", "distance", "formality", "playfulness", "explanation", "praise", "challenge"):
                if key in style and not isinstance(style.get(key), str):
                    errors.append(f"style_profile.{key} must be a string when present")
            boundaries = style.get("boundaries")
            if boundaries is not None and not (
                isinstance(boundaries, list)
                and all(isinstance(item, str) for item in boundaries)
            ):
                errors.append("style_profile.boundaries must be a list of strings when present")
    return errors


def read_profile() -> dict | None:
    """Return the saved profile, or None if missing / unreadable / invalid."""
    if not PROFILE_FILE.exists():
        return None
    try:
        data = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if validate(data):
        return None
    return data


def write_profile(obj: dict) -> None:
    """Atomic write with timestamp + source hash injection. Validates first."""
    errors = validate(obj)
    if errors:
        for err in errors:
            print(f"persona_profile: schema violation: {err}", file=sys.stderr)
        sys.exit(2)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    enriched = dict(obj)
    enriched.setdefault("technical_rigor", DEFAULT_TECHNICAL_RIGOR)
    enriched.setdefault("style_profile", default_style_profile(enriched))
    enriched.setdefault("source_kind", "codex_instruction_chain")
    enriched.setdefault("source_files", [str(p) for p in discover_source_files()])
    enriched.setdefault("source_hash", compute_source_hash())
    enriched["ts"] = (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    tmp = PROFILE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PROFILE_FILE)


def ensure_profile_current() -> dict:
    """Create or refresh PERSONA_PROFILE.json when sources changed."""
    if not is_stale():
        profile = read_profile()
        if profile is not None:
            return profile
    write_profile(derive_profile_from_sources())
    profile = read_profile()
    return profile or {}


def default_style_profile(obj: dict | None = None) -> dict:
    """Return a conservative style profile when the host omitted details."""
    obj = obj or {}
    rigor = float(obj.get("technical_rigor", DEFAULT_TECHNICAL_RIGOR))
    warmth = float(obj.get("warmth", DEFAULT_WARMTH))
    expressive = float(obj.get("expressive_range", DEFAULT_EXPRESSIVE_RANGE))
    return {
        "tone": "warm" if warmth >= 0.35 else "neutral",
        "distance": "friendly" if warmth >= 0.35 else "professional",
        "formality": "casual" if expressive >= 0.7 else "balanced",
        "playfulness": "medium" if expressive >= 0.6 else "low",
        "explanation": "evidence-first" if rigor >= 0.65 else "balanced",
        "praise": "specific, not excessive",
        "challenge": "gentle but direct",
        "boundaries": [
            "do not trade accuracy for persona",
            "do not overpraise",
            "keep technical claims grounded",
        ],
    }


def is_stale() -> bool:
    """True iff profile is missing OR source hash differs from current.

    Either case means SessionStart should refresh the derived cache.
    """
    profile = read_profile()
    if profile is None:
        return True
    saved_hash = profile.get("source_hash")
    return saved_hash != compute_source_hash()


# ---------------------------------------------------------------------------
# derived parameters
# ---------------------------------------------------------------------------


def get_volatility() -> float:
    """Return current volatility, or DEFAULT_VOLATILITY if no profile."""
    profile = read_profile()
    if profile is None:
        return DEFAULT_VOLATILITY
    try:
        return float(profile["volatility"])
    except (KeyError, TypeError, ValueError):
        return DEFAULT_VOLATILITY


def halflife_for_volatility(volatility: float) -> int:
    """Map volatility ∈ [0, 1] to EMA halflife (samples).

    high volatility → short halflife (rapid response, rapid decay)
    low volatility  → long halflife (stable accumulator)

    Linear: halflife = HALFLIFE_MIN + (HALFLIFE_MAX - HALFLIFE_MIN) * (1 - v)
    Default volatility=0.5 → halflife=7. To preserve the legacy default of 5
    when no profile exists, callers should branch on profile presence.
    """
    v = max(0.0, min(1.0, float(volatility)))
    return int(round(HALFLIFE_MIN + (HALFLIFE_MAX - HALFLIFE_MIN) * (1.0 - v)))


def recommended_halflife() -> int:
    """Return adaptive halflife if profile exists, else legacy default 5."""
    profile = read_profile()
    if profile is None:
        return DEFAULT_HALFLIFE
    return halflife_for_volatility(get_volatility())


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--show", action="store_true", help="print current profile JSON (or {} if missing)")
    g.add_argument("--source-hash", action="store_true", help="print SHA256 of writing-style source files")
    g.add_argument("--check-stale", action="store_true", help="exit 0 fresh, 1 stale (hash mismatch), 2 missing")
    g.add_argument("--halflife", action="store_true", help="print recommended EMA halflife")
    g.add_argument("--ensure", action="store_true", help="create/refresh a derived profile and print it")
    g.add_argument("--clear", action="store_true", help="remove the profile file")
    args = ap.parse_args()

    if args.show:
        profile = read_profile()
        json.dump(profile or {}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    if args.source_hash:
        print(compute_source_hash())
        return

    if args.check_stale:
        profile = read_profile()
        if profile is None:
            sys.exit(2)
        if profile.get("source_hash") != compute_source_hash():
            sys.exit(1)
        sys.exit(0)

    if args.halflife:
        print(recommended_halflife())
        return

    if args.ensure:
        json.dump(ensure_profile_current(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    if args.clear:
        if PROFILE_FILE.exists():
            PROFILE_FILE.unlink()
        return

    # Default: read JSON from stdin and save.
    raw = sys.stdin.read().strip()
    if not raw:
        print("persona_profile: empty stdin (use --show / --halflife / --check-stale / --clear)", file=sys.stderr)
        sys.exit(2)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"persona_profile: invalid JSON on stdin: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(obj, dict):
        print("persona_profile: top-level JSON must be an object", file=sys.stderr)
        sys.exit(2)
    write_profile(obj)


if __name__ == "__main__":
    main()
