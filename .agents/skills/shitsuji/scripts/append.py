#!/usr/bin/env python3
"""Append-only writer for HISTORY.jsonl with fcntl-based locking.

Reads one JSON object from stdin, prepends a UTC ISO-8601 timestamp under
the key "ts", validates the schema, and appends the result as a single
line to data/HISTORY.jsonl. Portable across macOS/Linux (uses Python's
fcntl, not the GNU `flock` CLI).

Mode B schema (Phase 9d):
    {
      "valence":   float in [-1.0, 1.0],
      "arousal":   float in [-1.0, 1.0],
      "dominance": float in [-1.0, 1.0],
      "primary":   one of: joy|trust|fear|surprise|sadness|disgust|anger|anticipation|neutral,
      "secondary": same set or null,
      "intensity": float in [0.0, 1.0],
      "confidence": float in [0.0, 1.0],
      "rationale": str,
      "user_emotion": {
        "valence": float in [-1.0, 1.0],
        "arousal": float in [-1.0, 1.0],
        "dominance": float in [-1.0, 1.0],
        "primary": same set as above,
        "rationale": str
      }
    }

Auto-annotated on save:
    - "ts" — UTC ISO-8601 timestamp
    - "dyad" — Plutchik dyad name when (primary, secondary) resolves; null otherwise

Antithesis pairs (joy↔sadness, trust↔disgust, fear↔anger,
surprise↔anticipation) are rejected with exit 2 — they can't co-occur
on the Plutchik wheel.

Set SHITSUJI_SCHEMA_LENIENT=1 to log violations to stderr but still
write the entry (transition mode for upgrading older HISTORY.jsonl files).
Default behavior is to reject malformed entries with exit 2.

Privacy:
    Set SHITSUJI_REDACT_USER_TEXT=1 to truncate `rationale` and
    `user_emotion.rationale` to a 16-char SHA256 prefix before writing.

Usage:
    echo '{...full schema...}' | append.py
    append.py < event.json

Exit codes:
    0 success
    1 lock timeout / IO error
    2 invalid JSON or schema violation (strict mode)
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SHITSUJI_DATA_DIR") or (SKILL_DIR / "data"))
HISTORY_FILE = DATA_DIR / "HISTORY.jsonl"
LOCK_TIMEOUT_SEC = 5.0

PRIMARY_LABELS = {
    "joy", "trust", "fear", "surprise",
    "sadness", "disgust", "anger", "anticipation", "neutral",
}

sys.path.insert(0, str(SKILL_DIR / "scripts"))
import dyad  # noqa: E402  — sibling module after sys.path insert


# ---------------------------------------------------------------------------
# locking
# ---------------------------------------------------------------------------

def acquire_lock_or_die(fd: int) -> None:
    deadline = time.monotonic() + LOCK_TIMEOUT_SEC
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                print(
                    f"append.py: failed to acquire {HISTORY_FILE} within "
                    f"{LOCK_TIMEOUT_SEC:.0f}s",
                    file=sys.stderr,
                )
                sys.exit(1)
            time.sleep(0.05)


# ---------------------------------------------------------------------------
# schema validation (P0-1)
# ---------------------------------------------------------------------------

def _is_float_in(value, lo: float, hi: float) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return lo <= v <= hi


def validate_entry(obj: dict) -> list[str]:
    """Return list of human-readable violations; empty list = valid."""
    errors: list[str] = []
    for k in ("valence", "arousal", "dominance"):
        if not _is_float_in(obj.get(k), -1.0, 1.0):
            errors.append(f"{k} must be float in [-1.0, 1.0], got {obj.get(k)!r}")
    primary = obj.get("primary")
    if primary not in PRIMARY_LABELS:
        errors.append(
            f"primary must be one of {sorted(PRIMARY_LABELS)}, "
            f"got {primary!r}"
        )
    secondary = obj.get("secondary")
    if secondary is not None and secondary not in PRIMARY_LABELS:
        errors.append(f"secondary must be null or one of label set, got {secondary!r}")
    # Reject antithesis pairs (joy↔sadness etc) — they're contradictory by Plutchik.
    if (
        primary in PRIMARY_LABELS
        and secondary in PRIMARY_LABELS
        and primary != "neutral"
        and secondary is not None
        and secondary != "neutral"
        and dyad.is_antithesis(primary, secondary)
    ):
        errors.append(
            f"primary={primary!r} + secondary={secondary!r} is an antithesis "
            f"pair (opposite ends of Plutchik wheel) — these cannot co-occur. "
            f"Pick one as primary and set secondary to null or a non-opposite label."
        )
    for k in ("intensity", "confidence"):
        if not _is_float_in(obj.get(k), 0.0, 1.0):
            errors.append(f"{k} must be float in [0.0, 1.0], got {obj.get(k)!r}")
    if not isinstance(obj.get("rationale"), str):
        errors.append("rationale must be a string")
    ue = obj.get("user_emotion")
    if not isinstance(ue, dict):
        errors.append(
            "user_emotion sub-object is required (Mode B invariant). "
            "Set SHITSUJI_SCHEMA_LENIENT=1 to write anyway during transition."
        )
    else:
        for k in ("valence", "arousal", "dominance"):
            if not _is_float_in(ue.get(k), -1.0, 1.0):
                errors.append(
                    f"user_emotion.{k} must be float in [-1.0, 1.0], "
                    f"got {ue.get(k)!r}"
                )
        if ue.get("primary") not in PRIMARY_LABELS:
            errors.append(
                f"user_emotion.primary must be one of label set, "
                f"got {ue.get('primary')!r}"
            )
        if not isinstance(ue.get("rationale"), str):
            errors.append("user_emotion.rationale must be a string")
    return errors


def annotate_dyad(obj: dict) -> dict:
    """Add a top-level `dyad` field derived from primary + secondary.

    Returns a shallow-copied dict with `dyad` set to:
        - the dyad name (e.g. "love", "submission") when both labels resolve
        - null otherwise (neutral, missing secondary, same labels, antithesis)

    Idempotent: if `dyad` is already present, it is overwritten with the
    canonical value.
    """
    primary = obj.get("primary")
    secondary = obj.get("secondary")
    name: str | None = None
    if primary and secondary:
        name = dyad.dyad_name(primary, secondary)
    out = dict(obj)
    out["dyad"] = name
    return out


# ---------------------------------------------------------------------------
# privacy redaction (P2-4)
# ---------------------------------------------------------------------------

def _redact(text: str) -> str:
    """SHA256-prefix redaction of free-text rationale (16 hex chars)."""
    if not isinstance(text, str) or not text:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"redacted:{digest}"


def maybe_redact(obj: dict) -> dict:
    if os.environ.get("SHITSUJI_REDACT_USER_TEXT") != "1":
        return obj
    obj = dict(obj)
    obj["rationale"] = _redact(obj.get("rationale", ""))
    ue = obj.get("user_emotion")
    if isinstance(ue, dict):
        ue = dict(ue)
        ue["rationale"] = _redact(ue.get("rationale", ""))
        obj["user_emotion"] = ue
    return obj


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.touch(exist_ok=True)

    raw = sys.stdin.read().strip()
    if not raw:
        print("append.py: empty stdin", file=sys.stderr)
        sys.exit(2)

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"append.py: invalid JSON on stdin: {e}", file=sys.stderr)
        sys.exit(2)

    if not isinstance(obj, dict):
        print("append.py: top-level JSON must be an object", file=sys.stderr)
        sys.exit(2)

    # Schema validation (P0-1).
    errors = validate_entry(obj)
    lenient = os.environ.get("SHITSUJI_SCHEMA_LENIENT") == "1"
    if errors:
        for err in errors:
            print(f"append.py: schema violation: {err}", file=sys.stderr)
        if not lenient:
            sys.exit(2)
        # Lenient mode writes the entry anyway after logging.

    obj = maybe_redact(obj)
    obj = annotate_dyad(obj)

    ts = (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    enriched = {"ts": ts, **obj}
    line = json.dumps(enriched, ensure_ascii=False)

    with open(HISTORY_FILE, "a+", encoding="utf-8") as f:
        acquire_lock_or_die(f.fileno())
        try:
            # Atomic-ish write with fsync to mitigate partial-line data loss
            # on power loss / SIGKILL during write.
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    main()
