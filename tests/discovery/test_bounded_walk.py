# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from observal_cli.discovery.adapter_support import RichAdapterScanner
from observal_cli.discovery.bounded_walk import AggregateDiscoveryBudget, BoundedWalker, WalkLimits
from observal_cli.discovery.models import DiagnosticCode, DiscoveryScope

if TYPE_CHECKING:
    import pytest


def _codes(walker: BoundedWalker) -> list[DiagnosticCode]:
    return [item.code for item in walker.diagnostics]


def test_walk_is_deterministic_and_does_not_follow_directory_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "z").mkdir(parents=True)
    (root / "a").mkdir()
    (root / "z" / "SKILL.md").write_text("z")
    (root / "a" / "SKILL.md").write_text("a")
    (root / "linked-dir").symlink_to(root / "z", target_is_directory=True)

    walker = BoundedWalker(root, provider="test")

    assert [path.parent.name for path in walker.files(root, name="SKILL.md")] == ["a", "z"]


def test_unrelated_files_do_not_hide_matches_or_emit_symlink_diagnostics(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    for index in range(4):
        (root / f"noise-{index}.txt").write_text("not metadata")
    (root / "unrelated-link.txt").symlink_to(tmp_path / "outside")
    (root / "SKILL.md").write_text("real skill")
    walker = BoundedWalker(root, provider="test", limits=WalkLimits(max_files_per_root=1))

    assert [path.name for path in walker.files(root, name="SKILL.md")] == ["SKILL.md"]
    assert walker.read_text(root / "SKILL.md") == "real skill"
    assert walker.diagnostics == []
    assert walker.budget.files == 1
    assert walker.budget.entries == 6


def test_file_symlinks_must_resolve_inside_approved_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "inside.json"
    inside.write_text("{}")
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (root / "safe.json").symlink_to(inside)
    (root / "escape.json").symlink_to(outside)

    walker = BoundedWalker(root, provider="test")
    paths = list(walker.files(root, suffix=".json"))

    assert [path.name for path in paths] == ["inside.json", "safe.json"]
    assert DiagnosticCode.SYMLINK_ESCAPE in _codes(walker)


def test_size_and_file_limits_preserve_partial_results(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("ok")
    (root / "b.txt").write_text("too large")
    deep = root
    for index in range(4):
        deep = deep / str(index)
        deep.mkdir()
    (deep / "deep.txt").write_text("deep")

    size_walker = BoundedWalker(root, provider="test", limits=WalkLimits(max_file_bytes=3))
    assert size_walker.read_text(root / "a.txt") == "ok"
    assert size_walker.read_text(root / "b.txt") is None
    assert DiagnosticCode.METADATA_TOO_LARGE in _codes(size_walker)

    limited = BoundedWalker(root, provider="test", limits=WalkLimits(max_files_per_root=2))
    assert list(limited.files(root, suffix=".txt"))
    assert DiagnosticCode.ITEM_LIMIT_REACHED in _codes(limited)


def test_read_diagnostic_does_not_embed_absolute_exception_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    metadata = root / "private.json"
    metadata.write_text("{}")
    original_open = os.open

    def fail_for_metadata(path, *args, **kwargs):
        if Path(path) == metadata.resolve():
            raise PermissionError(13, "permission denied", str(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", fail_for_metadata)
    walker = BoundedWalker(root, provider="test")

    assert walker.read_text(metadata) is None
    assert str(tmp_path) not in walker.diagnostics[0].message
    assert walker.diagnostics[0].source == "<external>/private.json"


def test_invalid_utf8_is_malformed_not_permission_denied(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(b"{\xff}")
    walker = BoundedWalker(tmp_path, provider="test")

    assert walker.read_text(path) is None
    assert _codes(walker) == [DiagnosticCode.METADATA_MALFORMED]
    assert "UTF-8" in walker.diagnostics[0].message


def test_explicit_utf8_read_preserves_non_ascii_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "skill.md"
    path.write_text("café", encoding="utf-8")
    original_read_text = type(path).read_text

    def read_with_non_utf8_locale(file: Path, *args, **kwargs):
        assert kwargs.get("encoding") == "utf-8"
        return original_read_text(file, *args, **kwargs)

    monkeypatch.setattr(type(path), "read_text", read_with_non_utf8_locale)
    assert BoundedWalker(tmp_path, provider="test").read_text(path) == "café"


def test_depth_limit_stops_deep_metadata(tmp_path: Path) -> None:
    root = tmp_path / "root"
    deep = root
    for index in range(4):
        deep = deep / str(index)
        deep.mkdir(parents=True)
    (deep / "deep.json").write_text("{}")
    walker = BoundedWalker(root, provider="test", limits=WalkLimits(max_depth=2))

    assert list(walker.files(root, suffix=".json")) == []
    assert DiagnosticCode.RECURSION_LIMIT_REACHED in _codes(walker)


def test_aggregate_root_limit_emits_one_stable_diagnostic(tmp_path: Path) -> None:
    budget = AggregateDiscoveryBudget(max_roots=1)
    first = BoundedWalker(tmp_path / "one", provider="test", budget=budget)
    second = BoundedWalker(tmp_path / "two", provider="test", budget=budget)
    third = BoundedWalker(tmp_path / "three", provider="test", budget=budget)

    assert not first.stopped
    assert second.stopped and third.stopped
    diagnostics = second.diagnostics + third.diagnostics
    assert [item.code for item in diagnostics] == [DiagnosticCode.APPROVED_ROOT_LIMIT_REACHED]


def test_local_limits_are_reported_for_each_affected_walker(tmp_path: Path) -> None:
    budget = AggregateDiscoveryBudget()
    diagnostics = []
    for provider in ("cursor", "claude-code"):
        root = tmp_path / provider
        root.mkdir()
        (root / "a.json").write_text("{}")
        (root / "b.json").write_text("{}")
        walker = BoundedWalker(root, provider=provider, budget=budget, limits=WalkLimits(max_files_per_root=1))
        list(walker.files(root, suffix=".json"))
        diagnostics.extend(walker.diagnostics)
    assert [(item.provider, item.code) for item in diagnostics] == [
        ("cursor", DiagnosticCode.ITEM_LIMIT_REACHED),
        ("claude-code", DiagnosticCode.ITEM_LIMIT_REACHED),
    ]
    assert DiagnosticCode.ITEM_LIMIT_REACHED not in budget.emitted_limits


def test_entry_limits_bound_unrelated_traversal_and_aggregate_usage(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    for index in range(4):
        (root / f"noise-{index}.txt").write_text("unrelated")
    walker = BoundedWalker(root, provider="test", limits=WalkLimits(max_entries_per_root=2))
    assert list(walker.files(root, name="SKILL.md")) == []
    assert _codes(walker) == [DiagnosticCode.ITEM_LIMIT_REACHED]

    budget = AggregateDiscoveryBudget(max_entries=1)
    first = BoundedWalker(root, provider="first", budget=budget)
    second = BoundedWalker(root, provider="second", budget=budget)
    list(first.files(root, name="SKILL.md"))
    list(second.files(root, name="SKILL.md"))
    assert [d.code for d in first.diagnostics + second.diagnostics] == [DiagnosticCode.COLLECTION_ENTRY_LIMIT_REACHED]
    assert budget.entries == 0  # An oversized directory is skipped, not partially selected in filesystem order.


def test_enumeration_is_capped_before_sorting_a_large_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    enumerated = []

    def fake_entries():
        for index in range(100_000):
            enumerated.append(index)
            yield object()

    monkeypatch.setattr(
        "observal_cli.discovery.bounded_walk.os.scandir", lambda _directory: nullcontext(fake_entries())
    )
    walker = BoundedWalker(root, provider="test", limits=WalkLimits(max_entries_per_root=2))

    assert list(walker.files(root, name="SKILL.md")) == []
    assert enumerated == [0, 1, 2]
    assert walker.budget.entries == 0
    assert _codes(walker) == [DiagnosticCode.ITEM_LIMIT_REACHED]


def test_aggregate_file_and_evidence_limits_emit_single_diagnostics(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.json").write_text("{}")
    (root / "b.json").write_text("{}")
    file_budget = AggregateDiscoveryBudget(max_files=1)
    first = BoundedWalker(root, provider="test", budget=file_budget)
    second = BoundedWalker(root, provider="test", budget=file_budget)
    list(first.files(root))
    list(second.files(root))
    file_diagnostics = first.diagnostics + second.diagnostics
    assert [item.code for item in file_diagnostics].count(DiagnosticCode.COLLECTION_FILE_LIMIT_REACHED) == 1

    evidence_budget = AggregateDiscoveryBudget(max_evidence=1)
    scanner = RichAdapterScanner(
        harness="test",
        scope=DiscoveryScope.PROJECT,
        root=root,
        project_dir=root,
        budget=evidence_budget,
    )
    scanner.add_agent_document(root / "a.json", name="one")
    scanner.add_agent_document(root / "b.json", name="two")
    result = scanner.finish()
    assert len(result.evidence) == 1
    assert [item.code for item in result.diagnostics].count(DiagnosticCode.EVIDENCE_LIMIT_REACHED) == 1


def test_deadline_stops_traversal_with_diagnostic(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "item.json").write_text("{}")
    ticks = iter([0.0, 2.0, 2.0])
    walker = BoundedWalker(
        root,
        provider="test",
        limits=WalkLimits(adapter_deadline_seconds=1),
        clock=lambda: next(ticks),
    )

    assert list(walker.files(root)) == []
    assert _codes(walker) == [DiagnosticCode.ADAPTER_DEADLINE_EXCEEDED]


def test_read_text_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    fifo = root / "mcp.json"
    os.mkfifo(fifo)
    walker = BoundedWalker(root, provider="test")

    assert walker.read_text(fifo) is None
    assert DiagnosticCode.METADATA_MALFORMED in _codes(walker)


def test_read_text_caps_actual_bytes_read(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "big.json").write_text("x" * 20)
    walker = BoundedWalker(root, provider="test", limits=WalkLimits(max_file_bytes=10))

    assert walker.read_text(root / "big.json") is None
    assert DiagnosticCode.METADATA_TOO_LARGE in _codes(walker)


def test_child_directories_charges_entry_budget(tmp_path: Path) -> None:
    root = tmp_path / "root"
    for name in ("a", "b", "c", "d"):
        (root / name).mkdir(parents=True)
    walker = BoundedWalker(root, provider="test", budget=AggregateDiscoveryBudget(max_entries=1))

    assert walker.child_directories(root) is None
    assert DiagnosticCode.COLLECTION_ENTRY_LIMIT_REACHED in _codes(walker)
    assert walker.budget.entries == 0


def test_child_directories_honors_deadline(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    walker = BoundedWalker(root, provider="test", clock=lambda: 10.0, deadline=1.0)

    assert walker.child_directories(root) is None
    assert DiagnosticCode.ADAPTER_DEADLINE_EXCEEDED in _codes(walker)
