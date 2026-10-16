# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Session hooks that launch the consent-gated startup updater for Codex, Kiro and Cursor.

Installed by ``observal doctor patch`` next to the telemetry hooks, and placed
where an agent pull's own config merge cannot overwrite them:

* Codex ``~/.codex/hooks.json``: ``SessionStart`` (startup|resume) and
  ``SessionEnd``. An agent pull replaces only ``UserPromptSubmit``/``Stop``.
  Codex asks the user to trust new or changed hooks in ``/hooks`` first.
* Kiro ``~/.kiro/hooks/observal-updates.json``: its own v1 file with
  ``SessionStart``, because an agent pull rewrites ``observal.json`` whole.
  Read by Kiro IDE 1.0+ and the CLI V3 engine (``--agent-engine v3``).
* Cursor ``~/.cursor/hooks.json``: ``sessionStart`` and ``sessionEnd``. An agent
  pull replaces only ``beforeSubmitPrompt``/``stop``.

The hook itself does nothing unless the user ran ``observal unfreeze``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rich import print as rprint

from observal_cli.render import esc

MARKER = "observal_cli.hooks.harness_updates"
KIRO_FILENAME = "observal-updates.json"
KIRO_HOOK_NAME = "observal-startup-updates"


def _python_cmd() -> str:
    from observal_cli.harness_specs.codex_hooks_spec import _python_cmd as resolve

    return resolve()


def command(harness: str) -> str:
    return f"{_python_cmd()} -m {MARKER} --harness {harness}"


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeError):
        return None  # Never overwrite a config we cannot parse.
    return data if isinstance(data, dict) else None


def _ours(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    if MARKER in str(entry.get("command", "")):
        return True
    return any(isinstance(h, dict) and MARKER in str(h.get("command", "")) for h in entry.get("hooks") or [])


def _desired(harness: str) -> dict[str, list[dict]]:
    cmd = command(harness)
    if harness == "codex":
        return {
            "SessionStart": [
                {"matcher": "startup|resume", "hooks": [{"type": "command", "command": cmd, "timeout": 5}]}
            ],
            "SessionEnd": [{"hooks": [{"type": "command", "command": cmd, "timeout": 3}]}],
        }
    if harness == "cursor":
        return {
            "sessionStart": [{"command": cmd, "type": "command", "timeout": 5}],
            "sessionEnd": [{"command": cmd, "type": "command", "timeout": 5}],
        }
    raise ValueError(harness)


def _write(path: Path, data: dict, dry_run: bool, verb: str) -> None:
    from observal_cli.cmd_doctor import atomic_write

    rprint(f"  {'Would ' + verb.lower() if dry_run else verb} startup update hooks in {esc(path)}")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(data, indent=2) + "\n")


def patch(harness: str, dry_run: bool) -> bool:
    """Install or refresh this harness's startup-update hooks; True if anything changed."""
    home = Path.home()
    if harness == "kiro":
        if not (home / ".kiro").is_dir():
            return False
        path = home / ".kiro" / "hooks" / KIRO_FILENAME
        desired = {
            "version": "v1",
            "hooks": [
                {
                    "name": KIRO_HOOK_NAME,
                    "trigger": "SessionStart",
                    "action": {"type": "command", "command": command("kiro")},
                    "timeout": 5,
                }
            ],
        }
        if _read(path) == desired:
            return False
        _write(path, desired, dry_run, "Installed")
        return True
    config_dir = home / (".codex" if harness == "codex" else ".cursor")
    if not config_dir.is_dir():
        return False
    path = config_dir / "hooks.json"
    current = _read(path)
    if current is None:
        rprint(f"  [yellow]Cannot parse {esc(path)}; startup update hooks not installed[/yellow]")
        return False
    hooks = current.get("hooks") if isinstance(current.get("hooks"), dict) else {}
    merged = dict(hooks)
    for event, entries in _desired(harness).items():
        existing = merged.get(event) if isinstance(merged.get(event), list) else []
        merged[event] = [entry for entry in existing if not _ours(entry)] + entries
    result = {**current, "hooks": merged}
    if harness == "cursor":
        result["version"] = current.get("version", 1)
    if result == current:
        return False
    _write(path, result, dry_run, "Installed")
    return True


def cleanup(harness: str, dry_run: bool) -> bool:
    """Remove only Observal's startup-update hooks."""
    home = Path.home()
    if harness == "kiro":
        path = home / ".kiro" / "hooks" / KIRO_FILENAME
        if not path.exists():
            return False
        rprint(f"  {'Would remove' if dry_run else 'Removed'} {esc(path)}")
        if not dry_run:
            path.unlink(missing_ok=True)
        return True
    path = home / (".codex" if harness == "codex" else ".cursor") / "hooks.json"
    current = _read(path)
    if not current or not isinstance(current.get("hooks"), dict):
        return False
    hooks: dict = {}
    for event, entries in current["hooks"].items():
        kept = [entry for entry in entries if not _ours(entry)] if isinstance(entries, list) else entries
        if kept != [] or entries == []:  # Drop only lists our removal emptied.
            hooks[event] = kept
    if hooks == current["hooks"]:
        return False
    _write(path, {**current, "hooks": hooks}, dry_run, "Removed")
    return True


if __name__ == "__main__":  # pragma: no cover - manual inspection aid
    print(json.dumps(_desired(sys.argv[1]), indent=2))
