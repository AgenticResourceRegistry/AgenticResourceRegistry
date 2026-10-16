# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Reconcile local lockfile metadata with the active Observal registry."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from observal_cli import client
from observal_cli.lockfile import _update_registry, current_registry_url, read_registry_lockfile

_CANONICAL_FIELDS = ("name", "namespace", "slug", "qualified_name")
_ITEM_TYPES = {"agent", "mcp", "skill", "hook", "prompt", "sandbox"}


@dataclass
class LockfileChange:
    entry: dict
    field: str
    old: object
    new: object
    label: str


@dataclass
class LockfileReconciliation:
    data: dict
    server_url: str
    changes: list[LockfileChange] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def apply(self) -> bool:
        if not self.changes:
            return True
        if current_registry_url() != self.server_url:
            self.warnings.append("Registry changed during reconciliation; rerun `observal doctor`.")
            return False

        def commit(registry: dict) -> tuple[bool, bool]:
            # The registry request ran outside the file lock. Never write the
            # plan's stale complete lockfile over an intervening installation.
            planned = self.data["registries"].get(self.server_url, {"server_url": self.server_url, "harnesses": {}})
            if registry != planned:
                self.warnings.append("Installed state changed during reconciliation; rerun `observal doctor`.")
                return False, False
            for change in self.changes:
                change.entry[change.field] = change.new
            registry.clear()
            registry.update(planned)
            return True, True

        return _update_registry(commit)


def _normalize_type(value: str) -> str | None:
    item_type = value.strip().lower().removesuffix("s")
    return item_type if item_type in _ITEM_TYPES else None


def _entry_refs(registry: dict) -> dict[tuple[str, str], list[tuple[dict, str]]]:
    refs: dict[tuple[str, str], list[tuple[dict, str]]] = {}
    for harness, section in registry.get("harnesses", {}).items():
        for agent in section.get("agents", []):
            agent_id = str(agent.get("id") or "")
            if agent_id:
                refs.setdefault(("agent", agent_id), []).append((agent, f"{harness} agent {agent_id[:8]}"))
            for component in agent.get("components", []):
                component_type = _normalize_type(str(component.get("type") or ""))
                component_id = str(component.get("id") or "")
                if component_type and component_id:
                    refs.setdefault((component_type, component_id), []).append(
                        (component, f"{harness} agent component {component_id[:8]}")
                    )
        for item in section.get("standalone", []):
            item_type = _normalize_type(str(item.get("type") or ""))
            item_id = str(item.get("id") or "")
            if item_type and item_id:
                refs.setdefault((item_type, item_id), []).append((item, f"{harness} {item_type} {item_id[:8]}"))
    return refs


def _add_change(plan: LockfileReconciliation, entry: dict, label: str, field_name: str, new_value: object) -> None:
    if entry.get(field_name) != new_value:
        plan.changes.append(
            LockfileChange(
                entry=entry,
                field=field_name,
                old=entry.get(field_name),
                new=new_value,
                label=label,
            )
        )


def plan_lockfile_reconciliation() -> LockfileReconciliation:
    """Fetch canonical metadata for every current-registry lockfile UUID."""
    data, registry = read_registry_lockfile()
    plan = LockfileReconciliation(data=data, server_url=current_registry_url())
    refs = _entry_refs(registry)
    request_items = []
    valid_refs: dict[tuple[str, str], list[tuple[dict, str]]] = {}
    for (item_type, item_id), entries in refs.items():
        try:
            canonical_id = str(uuid.UUID(item_id))
        except ValueError:
            for entry, label in entries:
                _add_change(plan, entry, label, "registry_status", "invalid")
            continue
        valid_refs[(item_type, canonical_id)] = entries
        request_items.append({"type": item_type, "id": canonical_id})

    if not request_items:
        return plan

    results = client.post("/api/v1/registry/reconcile", {"items": request_items})
    for result in results:
        key = (str(result["type"]), str(result["id"]))
        entries = valid_refs.get(key, [])
        if not result.get("found"):
            for entry, label in entries:
                _add_change(plan, entry, label, "registry_status", "unavailable")
            continue
        for entry, label in entries:
            for field_name in _CANONICAL_FIELDS:
                new_value = result.get(field_name)
                if new_value is not None:
                    _add_change(plan, entry, label, field_name, new_value)
            _add_change(plan, entry, label, "registry_status", result.get("status") or "available")
    return plan
