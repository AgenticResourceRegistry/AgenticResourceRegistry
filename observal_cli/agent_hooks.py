# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Opt-in placement of a Claude Code agent's hooks in ``settings.json``, gated on the agent.

Claude Code runs hooks from an agent's frontmatter only in interactive
sessions. ``agent pull --hooks=settings`` moves the agent's command hooks into
the scope's ``settings.json`` instead, each wrapped by ``observal_cli.hook_gate``
so it runs only while that agent is active, headless included.

Ownership: every matcher group written here carries
``"_observal": {"kind": "agent-hook", "agent", "component_id", "digest"}``.
``digest`` is the SHA-256 of the event and the whole group without that key
(command, matcher, timeout, every option), as Observal last wrote it. A
reconcile touches only the groups owned by the agent being pulled:

* a clean owned group that is still wanted is kept, one no longer wanted is removed;
* an owned group whose content no longer matches its digest was edited by the
  user. It is never replaced or removed without an explicit force, and the
  pull reports it as a conflict. Deleting the ``_observal`` key hands the group
  to the user;
* user groups, other agents' groups and Observal's session-push groups are
  never touched.

Nothing here logs or stores a hook's input or output.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

from observal_cli.shared.utils import OBSERVAL_METADATA_KEY, atomic_write
from observal_shared.harness_registry import HARNESS_REGISTRY

AGENT_HOOK_KIND = "agent-hook"
PLACEMENTS = ("frontmatter", "settings")
ON_UNKNOWN = ("skip", "run")
# The lockfile placement of each gated hook component.
GATED_PLACEMENT = "gated_settings"

# Claude Code versions whose hook inputs were recorded and whose gated hooks were
# run end to end (tests/fixtures/component_insights/claude_code/hook_inputs and
# gate_session_*.jsonl). Shared with the server, which reports hook evidence from
# other versions as unverified; the opt-in only warns outside it.
TESTED_CLAUDE_CODE_MIN, TESTED_CLAUDE_CODE_MAX = HARNESS_REGISTRY["claude-code"]["hook_evidence_tested_versions"]

# Events where a hook's exit code 2 blocks what it guards. An unrecognized hook
# input skips the hook by default (--on-unknown skip), which then cannot block.
BLOCKING_EVENTS = frozenset({"PreToolUse", "PermissionRequest", "UserPromptSubmit", "Stop", "SubagentStop"})

# Claude Code's agent name, as it appears in the hook input's ``agent_type``.
_AGENT = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_EVENT = re.compile(r"[A-Za-z]{1,64}\Z")
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class SettingsError(Exception):
    """settings.json cannot be read or safely edited. The message is fixed text."""


@dataclass(frozen=True)
class AgentHook:
    """One agent command hook as it will be placed in settings.json."""

    component_id: str
    name: str
    event: str
    command: str  # the original command, as the agent file would carry it
    gated: str  # the settings command: the gate wrapping ``command``

    def group(self, agent: str) -> dict:
        base = {"hooks": [{"type": "command", "command": self.gated}]}
        return {
            **base,
            OBSERVAL_METADATA_KEY: {
                "kind": AGENT_HOOK_KIND,
                "agent": agent,
                "component_id": self.component_id,
                "digest": group_digest(self.event, base),
            },
        }


def valid_agent_name(agent: object) -> bool:
    return isinstance(agent, str) and bool(_AGENT.fullmatch(agent))


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.search(text or "")
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def claude_code_version() -> str | None:
    """``claude --version`` as ``X.Y.Z``, or None when it cannot be determined."""
    try:
        result = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=30, check=False, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    parsed = parse_version(result.stdout)
    return ".".join(map(str, parsed)) if parsed else None


def is_tested_version(version: str | None) -> bool:
    parsed = parse_version(version or "")
    return parsed is not None and TESTED_CLAUDE_CODE_MIN <= parsed <= TESTED_CLAUDE_CODE_MAX


def tested_range() -> str:
    low, high = (".".join(map(str, bound)) for bound in (TESTED_CLAUDE_CODE_MIN, TESTED_CLAUDE_CODE_MAX))
    return low if low == high else f"{low} to {high}"


def settings_location(scope: str, target_dir: Path) -> tuple[Path, str]:
    """The settings file Claude Code reads for the scope, and its layer display path."""
    if scope == "user":
        return Path.home() / ".claude" / "settings.json", "user:settings.json"
    return target_dir / ".claude" / "settings.json", "project:.claude/settings.json"


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def group_digest(event: str, group: dict) -> str:
    """SHA-256 of the event and the whole matcher group, without Observal's metadata."""
    content = {key: value for key, value in group.items() if key != OBSERVAL_METADATA_KEY}
    return hashlib.sha256(_canonical([event, content])).hexdigest()


def agent_hook_metadata(group: object) -> dict | None:
    """The ``_observal`` metadata of an Observal-owned agent hook group, else None."""
    if not isinstance(group, dict):
        return None
    meta = group.get(OBSERVAL_METADATA_KEY)
    if isinstance(meta, dict) and meta.get("kind") == AGENT_HOOK_KIND:
        return meta
    return None


def expected_agent_name(local_name: str) -> str:
    """The Claude Code agent name the server writes for ``local_name`` (``services.shared.utils.sanitize_name``)."""
    if re.fullmatch(r"[a-zA-Z0-9_-]+", local_name):
        return local_name
    return re.sub(r"[^a-zA-Z0-9_-]", "-", local_name)


def existing_placement(path: Path, agent: str) -> str | None:
    """The ``--on-unknown`` policy of gated hooks already owned by ``agent`` in this settings file, else None.

    Lets a pull keep an earlier ``--hooks=settings`` choice that the local
    lockfile does not record (a teammate's committed project settings, or a
    lockfile write that failed after settings.json was written). An unreadable
    file reports nothing; the pull's own planning refuses it when it matters.
    """
    try:
        settings = read_settings(path)
    except SettingsError:
        return None
    hooks = (settings or {}).get("hooks") or {}
    policies = {
        "run" if command.endswith(" --on-unknown run") else "skip"
        for groups in hooks.values()
        for group in groups
        if (agent_hook_metadata(group) or {}).get("agent") == agent
        for hook in (group.get("hooks") if isinstance(group.get("hooks"), list) else [])
        if isinstance(hook, dict) and isinstance(command := hook.get("command"), str)
    }
    if not policies:
        return None
    return "run" if policies == {"run"} else "skip"


def is_edited(event: str, group: dict) -> bool:
    meta = agent_hook_metadata(group) or {}
    return meta.get("digest") != group_digest(event, group)


def build_agent_hooks(agent: str, bindings: list[dict], components: list[dict], on_unknown: str) -> list[AgentHook]:
    """One gated hook per bound command hook; each must map to a pinned hook component.

    Raises ValueError (fixed text) when a hook cannot be placed safely.
    """
    from observal_cli.hook_gate import gated_command

    if not valid_agent_name(agent):
        raise ValueError("The agent name cannot be matched against Claude Code's agent_type.")
    if on_unknown not in ON_UNKNOWN:
        raise ValueError("--on-unknown must be skip or run.")
    by_name: dict[str, list[dict]] = {}
    for component in components:
        if component.get("type") == "hook" and component.get("local_name") and component.get("id"):
            by_name.setdefault(component["local_name"], []).append(component)
    hooks: list[AgentHook] = []
    seen: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        name, event, command = binding.get("name"), binding.get("event"), binding.get("command")
        if not isinstance(event, str) or not _EVENT.fullmatch(event) or not isinstance(command, str) or not command:
            raise ValueError("A hook binding from the server is malformed.")
        matches = by_name.get(name) if isinstance(name, str) else None
        if not matches or len(matches) != 1 or name in seen:
            raise ValueError("An agent hook does not map to exactly one pinned hook component.")
        seen.add(name)
        hooks.append(
            AgentHook(
                component_id=str(matches[0]["id"]),
                name=name,
                event=event,
                command=command,
                gated=gated_command(agent, command, on_unknown=on_unknown),
            )
        )
    return hooks


def read_settings(path: Path) -> dict | None:
    """The settings object, or None when the file does not exist.

    Raises SettingsError when it exists but is not a JSON object whose ``hooks``
    (if present) maps events to lists: such a file cannot be edited safely.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as error:
        raise SettingsError("settings.json cannot be read.") from error
    try:
        data = json.loads(text)
    except ValueError as error:
        raise SettingsError("settings.json is not valid JSON.") from error
    if not isinstance(data, dict):
        raise SettingsError("settings.json is not a JSON object.")
    hooks = data.get("hooks")
    if hooks is not None and (
        not isinstance(hooks, dict) or not all(isinstance(groups, list) for groups in hooks.values())
    ):
        raise SettingsError("settings.json has a hooks section Observal cannot edit safely.")
    return data


@dataclass
class HookPlan:
    path: Path
    display: str
    original: dict | None
    data: dict | None
    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    kept: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.data is not None and self.data != self.original

    def summary(self) -> dict:
        return {
            "settings_file": str(self.path),
            "added": self.added,
            "removed": self.removed,
            "kept": self.kept,
            "conflicts": self.conflicts,
            "changed": self.changed,
        }


def plan_settings(
    path: Path,
    display: str,
    settings: dict | None,
    agent: str,
    hooks: list[AgentHook],
    *,
    owners: tuple[str, ...] = (),
    force: bool = False,
) -> HookPlan:
    """Reconcile the groups owned by ``agent`` (and any earlier names in ``owners``) with ``hooks``.

    Pure: returns the new settings object without writing it. ``conflicts`` lists
    edited owned groups; with ``force`` they are replaced or removed instead.
    """
    names = {agent, *owners}
    desired = [(hook.event, hook.group(agent), hook) for hook in hooks]
    data = copy.deepcopy(settings) if settings is not None else {}
    plan = HookPlan(path, display, settings, None)
    current = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    satisfied: set[int] = set()
    edited_components: set[str] = set()
    removals: list[tuple[str, int]] = []
    for event, groups in current.items():
        for index, group in enumerate(groups):
            meta = agent_hook_metadata(group)
            if meta is None or meta.get("agent") not in names:
                continue
            component = str(meta.get("component_id") or "")
            entry = {"event": event, "component_id": component, "agent": str(meta.get("agent"))}
            if is_edited(event, group):
                if force:
                    removals.append((event, index))
                    plan.removed.append(entry | {"edited": True})
                else:
                    plan.conflicts.append(entry)
                    edited_components.add(component)
                continue
            match = next(
                (
                    position
                    for position, (want_event, want, _hook) in enumerate(desired)
                    if position not in satisfied and want_event == event and want == group
                ),
                None,
            )
            if match is None:
                removals.append((event, index))
                plan.removed.append(entry)
            else:
                satisfied.add(match)
                plan.kept.append(entry | {"name": desired[match][2].name})
    for event, index in sorted(removals, key=lambda item: (item[0], -item[1])):
        del current[event][index]
        if not current[event]:
            del current[event]
    for position, (event, group, hook) in enumerate(desired):
        if position in satisfied or hook.component_id in edited_components:
            continue
        current.setdefault(event, []).append(group)
        plan.added.append({"event": event, "component_id": hook.component_id, "name": hook.name})
    if current:
        data["hooks"] = current
    elif "hooks" in data and (settings or {}).get("hooks"):
        del data["hooks"]
    if settings is None and not current:
        plan.data = None  # nothing to write and no file to create
    else:
        plan.data = data
    return plan


@dataclass(frozen=True)
class AgentHookRequest:
    """Everything one pull needs to (re)plan its settings.json agent hooks.

    Planned once before any file is written (to refuse invalid settings or
    conflicts early) and again from a fresh read just before writing.
    """

    placement: str
    on_unknown: str
    path: Path
    display: str
    agent: str
    hooks: tuple[AgentHook, ...]
    owners: tuple[str, ...] = ()
    force: bool = False

    def plan(self) -> HookPlan:
        """Raises SettingsError when settings.json cannot be edited safely."""
        settings = read_settings(self.path)
        return plan_settings(
            self.path,
            self.display,
            settings,
            self.agent,
            list(self.hooks),
            owners=self.owners,
            force=self.force,
        )


def write_settings(path: Path, data: dict) -> None:
    """Atomic replace (temporary file plus rename) in the settings directory, keeping the file mode."""
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def measure_gate_startup(runs: int = 5) -> float | None:
    """Median milliseconds for one gated hook to decide "skip" on this machine, or None.

    This is what each gated hook adds to every matching event, even when its
    agent is not active.
    """
    import time

    from observal_cli.hook_gate import gated_command

    if sys.platform == "win32":
        return None
    command = gated_command("observal-measure", "true")
    stdin = json.dumps({"session_id": "measure", "hook_event_name": "PreToolUse"}).encode()
    samples: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        try:
            result = subprocess.run(
                ["/bin/sh", "-c", command], input=stdin, capture_output=True, timeout=30, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        samples.append((time.perf_counter() - start) * 1000)
    return round(median(samples), 1) if samples else None
