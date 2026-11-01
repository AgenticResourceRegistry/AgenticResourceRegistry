# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The project lock: ``observal.lock`` in a project directory.

``observal agent pull`` writes it next to the files it installs, and it is
meant to be committed. Anyone who pulls the same agent in that directory,
including teammates and CI, installs the exact agent version recorded here,
and therefore the exact component versions that agent version pinned, until
someone deliberately moves it with ``--upgrade`` or ``--version``.

``~/.observal/lockfile.json`` is different: it is this machine's record of what
is installed where. The project lock is what the project wants installed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

PROJECT_LOCK_FILE = "observal.lock"
PROJECT_LOCK_VERSION = 1


class ProjectLockError(RuntimeError):
    """The project lock exists but cannot be used."""


def path_for(directory: str | Path) -> Path:
    return Path(directory) / PROJECT_LOCK_FILE


def read(directory: str | Path) -> dict:
    """Read the project lock, or an empty one when the project has none."""
    path = path_for(directory)
    if not path.exists():
        return {"lock_version": PROJECT_LOCK_VERSION, "agents": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProjectLockError(f"Cannot read {path}: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("agents"), dict):
        raise ProjectLockError(f"{path} is not a valid Observal project lock")
    if data.get("lock_version") != PROJECT_LOCK_VERSION:
        raise ProjectLockError(f"{path} uses unsupported lock_version {data.get('lock_version')!r}")
    return data


def _usable(entry: object) -> bool:
    return isinstance(entry, dict) and bool(entry.get("version"))


def locked_agent(directory: str | Path, qualified_name: str, agent_id: str | None = None) -> dict | None:
    """The locked entry for one agent, keyed by its canonical namespace/slug.

    An agent that was renamed or transferred since the lock was written keeps
    its registry id, so fall back to matching the recorded id rather than
    silently dropping the pin.
    """
    agents = read(directory)["agents"]
    entry = agents.get(qualified_name)
    if _usable(entry):
        return entry
    if agent_id:
        return next(
            (candidate for candidate in agents.values() if _usable(candidate) and candidate.get("id") == agent_id),
            None,
        )
    return None


def record_agent(directory: str | Path, qualified_name: str, entry: dict) -> Path:
    """Record the agent version a pull installed, writing the lock atomically.

    Keys and components are sorted so the file diffs cleanly in review. An entry
    left under an agent's previous name (same id) is replaced, not duplicated.
    """
    data = read(directory)
    agent_id = entry.get("id")
    if agent_id:
        data["agents"] = {
            name: existing
            for name, existing in data["agents"].items()
            if name == qualified_name or not (isinstance(existing, dict) and existing.get("id") == agent_id)
        }
    data["agents"][qualified_name] = entry
    data["agents"] = dict(sorted(data["agents"].items()))
    path = path_for(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, indent=2) + "\n"
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
        temporary = Path(file.name)
        file.write(body)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def agent_entry(*, agent_id: str, version: str, lock_digest: str | None, components: list[dict]) -> dict:
    """The project lock entry for an installed agent version."""
    return {
        "id": agent_id,
        "version": version,
        "lock_digest": lock_digest,
        "components": sorted(
            (
                {
                    "type": component.get("type"),
                    "qualified_name": component.get("qualified_name"),
                    "version": component.get("version"),
                    "digest": component.get("digest"),
                }
                for component in components
            ),
            key=lambda component: (component["type"] or "", component["qualified_name"] or ""),
        ),
    }
