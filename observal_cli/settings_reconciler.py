# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Non-destructive reconciler for Claude Code settings.

Implements a Terraform-style declarative reconciliation:
  1. Read current state from ~/.claude/settings.json
  2. Compare against desired state from claude_code_hooks_spec
  3. Apply minimal diff: add missing, update stale, preserve foreign

Never deletes non-Observal hooks or env vars.  Identifies Observal
hooks by script path pattern, not by position or event name.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

from loguru import logger as optic

from observal_cli import config
from observal_cli.harness_specs.claude_code_hooks_spec import (
    HOOKS_SPEC_VERSION,
    MANAGED_ENV_KEYS,
)
from observal_cli.shared.utils import is_observal_matcher_group

CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def _load_claude_settings() -> dict:
    """Load ~/.claude/settings.json, returning {} on missing/corrupt."""
    if not CLAUDE_SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        optic.warning("Could not parse {}: {}", CLAUDE_SETTINGS_PATH, exc)
        return {}


def _save_claude_settings(settings: dict) -> None:
    """Write settings.json atomically (parent dir created if needed)."""
    CLAUDE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLAUDE_SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2) + "\n",
        encoding="utf-8",
    )


def reconcile_hooks(
    current_hooks: dict[str, list],
    desired_hooks: dict[str, list],
) -> tuple[dict[str, list], list[str]]:
    """Merge desired Observal hooks into current hooks non-destructively.

    Returns (merged_hooks, changes) where changes is a list of
    human-readable strings describing what was modified.
    """
    merged = copy.deepcopy(current_hooks)
    changes: list[str] = []

    # 1. For each desired event, reconcile matcher groups
    for event, desired_groups in desired_hooks.items():
        if event not in merged:
            # New event - add entirely
            merged[event] = copy.deepcopy(desired_groups)
            changes.append(f"+ {event}: added ({len(desired_groups)} handler(s))")
            continue

        current_groups = merged[event]

        # Partition current groups into Observal-managed and foreign
        foreign_groups = [g for g in current_groups if not is_observal_matcher_group(g)]
        observal_groups = [g for g in current_groups if is_observal_matcher_group(g)]

        # Check if Observal groups match desired (by JSON equality)
        if _groups_equal(observal_groups, desired_groups):
            continue  # Already up to date

        # Replace Observal groups with desired, keep foreign ones
        merged[event] = foreign_groups + copy.deepcopy(desired_groups)

        if observal_groups:
            changes.append(f"~ {event}: updated Observal hooks")
        else:
            changes.append(f"+ {event}: added Observal hooks")

    # 2. Events in current but not in desired - leave them alone
    #    (they might be non-Observal hooks, or events we no longer manage)

    return merged, changes


def reconcile_env(
    current_env: dict[str, str],
    desired_env: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    """Merge desired Observal env vars into current env.

    Only touches keys in MANAGED_ENV_KEYS.  Foreign env vars are
    preserved untouched.
    """
    merged = dict(current_env)
    changes: list[str] = []

    for key, value in desired_env.items():
        if key not in MANAGED_ENV_KEYS:
            continue
        old = merged.get(key)
        if old != value:
            merged[key] = value
            if old is None:
                changes.append(f"+ env.{key}")
            else:
                changes.append(f"~ env.{key}")

    return merged, changes


def reconcile(
    desired_hooks: dict[str, list],
    desired_env: dict[str, str],
    *,
    dry_run: bool = False,
) -> list[str]:
    """Full reconciliation: load settings, diff, write if changed.

    Returns list of change descriptions (empty = already up to date).
    If dry_run=True, computes changes but does not write.
    """
    optic.debug("settings reconcile: dry_run={}, desired_hooks={}", dry_run, len(desired_hooks))
    settings = _load_claude_settings()
    all_changes: list[str] = []

    # Reconcile hooks
    current_hooks = settings.get("hooks", {})
    merged_hooks, hook_changes = reconcile_hooks(current_hooks, desired_hooks)
    all_changes.extend(hook_changes)

    # Reconcile env
    current_env = settings.get("env", {})
    merged_env, env_changes = reconcile_env(current_env, desired_env)
    all_changes.extend(env_changes)

    if all_changes and not dry_run:
        settings["hooks"] = merged_hooks
        settings["env"] = merged_env
        _save_claude_settings(settings)

        # Record applied spec version
        config.save({"hooks_spec_version": HOOKS_SPEC_VERSION})

    if not dry_run:
        # Our own groups now equal the generated spec exactly: that equality is
        # the ownership evidence the startup refresh relies on.
        _record_if_generated(_load_claude_settings(), desired_hooks)

    return all_changes


# ---------------------------------------------------------------------------
# Startup refresh: replace only unedited Observal hook groups
# ---------------------------------------------------------------------------

RECORD_PATH = config.CONFIG_DIR / "managed-claude-hooks.json"
MAX_SETTINGS_BYTES = 2 * 1024 * 1024
_RECORD_HINT = "run `observal doctor patch --harness claude-code`"


def _group_hash(group: object) -> str:
    return hashlib.sha256(json.dumps(group, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _observal_hashes(hooks: object) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if not isinstance(hooks, dict):
        return result
    for event, groups in hooks.items():
        if isinstance(groups, list):
            found = sorted(_group_hash(g) for g in groups if isinstance(g, dict) and is_observal_matcher_group(g))
            if found:
                result[str(event)] = found
    return result


def _write_record(hashes: dict[str, list[str]]) -> None:
    RECORD_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = RECORD_PATH.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"schema": 1, "hooks": hashes}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, RECORD_PATH)


def _read_record() -> dict[str, list[str]] | None:
    try:
        info = RECORD_PATH.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 1024 * 1024:
            return None
        raw = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    hooks = raw.get("hooks") if isinstance(raw, dict) and raw.get("schema") == 1 else None
    if not isinstance(hooks, dict) or any(
        not isinstance(v, list) or any(not isinstance(h, str) for h in v) for v in hooks.values()
    ):
        return None
    return {str(k): list(v) for k, v in hooks.items()}


def _record_if_generated(settings: dict, desired_hooks: dict[str, list]) -> None:
    """Record our groups only when they are exactly what the spec generates."""
    try:
        current = settings.get("hooks", {})
        for event, desired in desired_hooks.items():
            ours = [g for g in current.get(event, []) if isinstance(g, dict) and is_observal_matcher_group(g)]
            if not _groups_equal(ours, desired):
                return
        _write_record({e: h for e, h in _observal_hashes(current).items() if e in desired_hooks})
    except (OSError, AttributeError, TypeError, ValueError):
        pass  # Best effort: no record just means manual updates.


def refresh_unedited() -> tuple[str, str]:
    """Bring our Claude Code hook groups up to the shipped spec without touching anything else.

    Returns (status, reason): "current", "updated" or "manual". Only groups that
    still hash to what Observal recorded are replaced; foreign hooks and every
    other setting are preserved. The write is atomic and abandoned if the file
    changed while it was being prepared.
    """
    from observal_cli.harness_specs.claude_code_hooks_spec import get_desired_hooks

    path = CLAUDE_SETTINGS_PATH
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured and Path(configured).expanduser() != path.parent:
        return "manual", "Claude Code uses a different config directory; " + _RECORD_HINT + " there."
    if not path.exists():
        return "current", ""
    try:
        if any(part.is_symlink() for part in (path, *path.parents)) or not stat.S_ISREG(path.lstat().st_mode):
            return "manual", "The Claude Code settings path is a link or not a regular file."
        if path.stat().st_size > MAX_SETTINGS_BYTES:
            return "manual", "The Claude Code settings file is unusually large."
        original = path.read_bytes()
        settings = json.loads(original.decode("utf-8"))
        mode = stat.S_IMODE(path.lstat().st_mode)
    except (OSError, ValueError, UnicodeError):
        return "manual", "The Claude Code settings file could not be read as JSON."
    if not isinstance(settings, dict) or not isinstance(settings.get("hooks", {}), dict):
        return "manual", "The Claude Code settings file has an unexpected hooks layout."
    desired = get_desired_hooks()
    current = settings.get("hooks", {})
    if not any(event in desired for event in _observal_hashes(current)):
        return "current", ""  # Never install hooks for someone who has none; that is `doctor patch`.
    merged, changes = reconcile_hooks(current, desired)
    if not changes:
        _record_if_generated(settings, desired)
        return "current", ""
    recorded = _read_record()
    if recorded is None:
        return "manual", "No ownership record exists for Observal's Claude Code hooks; " + _RECORD_HINT + " once."
    found = {e: h for e, h in _observal_hashes(current).items() if e in desired}
    if any(set(found.get(event, [])) - set(recorded.get(event, [])) for event in found):
        return "manual", "Observal's Claude Code hook entries were edited or added by hand; " + _RECORD_HINT + "."
    if any(set(recorded.get(event, [])) - set(found.get(event, [])) for event in recorded if event in desired):
        # A deliberately removed managed group is a local change, not drift to repair.
        return (
            "manual",
            "An Observal Claude Code hook entry was removed from your settings; " + _RECORD_HINT + " to restore it.",
        )
    settings["hooks"] = merged
    updated = (json.dumps(settings, indent=2) + "\n").encode("utf-8")
    if len(updated) > MAX_SETTINGS_BYTES:
        return "manual", "The updated Claude Code settings would be unusually large."
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=".settings.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(updated)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, mode)
        # Claude Code also writes this file. Abandon the update if it changed.
        if path.read_bytes() != original or any(part.is_symlink() for part in (path, *path.parents)):
            return "manual", "Claude Code settings changed while updating; nothing was written."
        os.replace(temporary, path)
    except OSError:
        return "manual", "The Claude Code settings file could not be written; nothing was changed."
    finally:
        temporary.unlink(missing_ok=True)
    try:
        reread = json.loads(path.read_text(encoding="utf-8"))
        if reread != settings:
            return "manual", "Claude Code settings did not read back as planned; inspect the file."
        _write_record({e: h for e, h in _observal_hashes(reread.get("hooks", {})).items() if e in desired})
        config.save({"hooks_spec_version": HOOKS_SPEC_VERSION})
    except (OSError, ValueError):
        return "manual", "Claude Code settings were updated but the ownership record could not be saved."
    return "updated", "; ".join(changes)[:200]


def needs_upgrade() -> bool:
    """Check if the applied hooks spec is older than the current version."""
    cfg = config.load()
    applied = cfg.get("hooks_spec_version", "0")
    return applied != HOOKS_SPEC_VERSION


def get_applied_version() -> str:
    """Return the hooks spec version currently applied."""
    cfg = config.load()
    return cfg.get("hooks_spec_version", "0")


def _groups_equal(a: list[dict], b: list[dict]) -> bool:
    """Compare two lists of matcher groups by normalized JSON."""
    return _normalize(a) == _normalize(b)


def _normalize(obj: object) -> object:
    """Recursively sort dicts for stable comparison."""
    if isinstance(obj, dict):
        return tuple(sorted((k, _normalize(v)) for k, v in obj.items()))
    if isinstance(obj, list):
        return tuple(_normalize(item) for item in obj)
    return obj
