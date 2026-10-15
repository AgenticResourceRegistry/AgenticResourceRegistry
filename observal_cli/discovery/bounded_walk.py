# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic, bounded filesystem traversal for harness discovery."""

from __future__ import annotations

import os
import stat
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING

from observal_cli.discovery.models import DiagnosticCode, DiagnosticSeverity, DiscoveryDiagnostic
from observal_cli.discovery.redact import make_diagnostic

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@dataclass(frozen=True)
class WalkLimits:
    max_files_per_root: int = 5_000
    max_entries_per_root: int = 25_000
    max_file_bytes: int = 1024 * 1024
    max_depth: int = 8
    adapter_deadline_seconds: float = 10.0


@dataclass
class AggregateDiscoveryBudget:
    """Aggregate accounting shared by rich harness collection."""

    max_roots: int = 256
    max_files: int = 25_000
    max_entries: int = 100_000
    max_evidence: int = 10_000
    max_diagnostics: int = 1_000
    ordinary_diagnostic_limit: int = 995
    roots: int = 0
    files: int = 0
    entries: int = 0
    evidence: int = 0
    diagnostics: int = 0
    emitted_limits: set[DiagnosticCode] = field(default_factory=set)


_AGGREGATE_LIMIT_CODES = frozenset(
    {
        DiagnosticCode.APPROVED_ROOT_LIMIT_REACHED,
        DiagnosticCode.COLLECTION_FILE_LIMIT_REACHED,
        DiagnosticCode.COLLECTION_ENTRY_LIMIT_REACHED,
        DiagnosticCode.EVIDENCE_LIMIT_REACHED,
        DiagnosticCode.DIAGNOSTIC_LIMIT_REACHED,
    }
)

_ACTIVE_BUDGET: ContextVar[AggregateDiscoveryBudget | None] = ContextVar("discovery_budget", default=None)
_ACTIVE_DEADLINE: ContextVar[float | None] = ContextVar("discovery_deadline", default=None)


@contextmanager
def discovery_budget(budget: AggregateDiscoveryBudget, *, deadline: float | None = None) -> Iterator[None]:
    """Apply aggregate limits to every walker created in this context."""
    budget_token = _ACTIVE_BUDGET.set(budget)
    deadline_token = _ACTIVE_DEADLINE.set(deadline)
    try:
        yield
    finally:
        _ACTIVE_DEADLINE.reset(deadline_token)
        _ACTIVE_BUDGET.reset(budget_token)


class BoundedWalker:
    """Inspect approved roots without escaping them or following directory links."""

    def __init__(
        self,
        root: Path,
        *,
        provider: str,
        limits: WalkLimits | None = None,
        budget: AggregateDiscoveryBudget | None = None,
        clock: Callable[[], float] = time.monotonic,
        deadline: float | None = None,
    ) -> None:
        self.root = root.expanduser().resolve(strict=False)
        self.provider = provider
        self.limits = limits or WalkLimits()
        self.budget = budget or _ACTIVE_BUDGET.get() or AggregateDiscoveryBudget()
        self._clock = clock
        local_deadline = deadline if deadline is not None else clock() + self.limits.adapter_deadline_seconds
        active_deadline = _ACTIVE_DEADLINE.get()
        self._deadline = min(local_deadline, active_deadline) if active_deadline is not None else local_deadline
        self._root_files = 0
        self._root_entries = 0
        self._seen_files: set[Path] = set()
        self._diagnostics: list[DiscoveryDiagnostic] = []
        self._emitted_local: set[DiagnosticCode] = set()
        self._stopped = False
        self._approve_root()

    @property
    def diagnostics(self) -> list[DiscoveryDiagnostic]:
        return list(self._diagnostics)

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def deadline(self) -> float:
        return self._deadline

    def halt(self) -> None:
        self._stopped = True

    def _approve_root(self) -> None:
        if self.budget.roots >= self.budget.max_roots:
            self.limit(DiagnosticCode.APPROVED_ROOT_LIMIT_REACHED, "approved discovery root limit reached")
            self._stopped = True
            return
        self.budget.roots += 1

    def diagnostic(
        self,
        code: DiagnosticCode,
        message: str,
        *,
        source: Path | None = None,
        severity: DiagnosticSeverity = DiagnosticSeverity.WARNING,
        limit: bool = False,
    ) -> None:
        if limit:
            emitted = self.budget.emitted_limits if code in _AGGREGATE_LIMIT_CODES else self._emitted_local
            if code in emitted:
                return
            emitted.add(code)
        elif self.budget.diagnostics >= self.budget.ordinary_diagnostic_limit:
            self.limit(DiagnosticCode.DIAGNOSTIC_LIMIT_REACHED, "discovery diagnostic limit reached")
            return
        if self.budget.diagnostics >= self.budget.max_diagnostics:
            return
        self._diagnostics.append(
            make_diagnostic(
                code=code,
                severity=severity,
                provider=self.provider,
                source=source,
                message=message,
            )
        )
        self.budget.diagnostics += 1

    def limit(self, code: DiagnosticCode, message: str, *, source: Path | None = None) -> None:
        self.diagnostic(code, message, source=source, limit=True)

    def _within_root(self, path: Path) -> bool:
        try:
            path.relative_to(self.root)
            return True
        except ValueError:
            return False

    def _deadline_ok(self) -> bool:
        if self._stopped:
            return False
        if self._clock() <= self._deadline:
            return True
        self.limit(DiagnosticCode.ADAPTER_DEADLINE_EXCEEDED, "adapter discovery deadline exceeded", source=self.root)
        self._stopped = True
        return False

    def _inspect(self, path: Path) -> Path | None:
        if not self._deadline_ok():
            return None
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            self.diagnostic(DiagnosticCode.PERMISSION_DENIED, "unable to resolve discovery path", source=path)
            return None
        if not self._within_root(resolved):
            code = DiagnosticCode.SYMLINK_ESCAPE if path.is_symlink() else DiagnosticCode.PATH_OUTSIDE_ROOT
            self.diagnostic(code, "discovery path resolves outside its approved root", source=path)
            return None
        inspected_path = path.absolute()
        if inspected_path in self._seen_files:
            return resolved
        if self._root_files >= self.limits.max_files_per_root:
            self.limit(DiagnosticCode.ITEM_LIMIT_REACHED, "approved root file limit reached", source=self.root)
            self._stopped = True
            return None
        if self.budget.files >= self.budget.max_files:
            self.limit(DiagnosticCode.COLLECTION_FILE_LIMIT_REACHED, "aggregate discovery file limit reached")
            self._stopped = True
            return None
        self._seen_files.add(inspected_path)
        self._root_files += 1
        self.budget.files += 1
        return resolved

    def read_text(self, path: Path) -> str | None:
        """Read one contained metadata file after size and deadline checks."""
        if not path.exists() and not path.is_symlink():
            return None
        resolved = self._inspect(path)
        if resolved is None:
            return None
        fd = -1
        try:
            # O_NONBLOCK keeps open() from hanging on a FIFO; the fstat check then rejects it.
            fd = os.open(resolved, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                self.diagnostic(
                    DiagnosticCode.METADATA_MALFORMED, "discovery metadata is not a regular file", source=path
                )
                return None
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                data = handle.read(self.limits.max_file_bytes + 1)
            if len(data) > self.limits.max_file_bytes:
                self.diagnostic(
                    DiagnosticCode.METADATA_TOO_LARGE,
                    f"metadata file exceeds {self.limits.max_file_bytes} byte limit",
                    source=path,
                )
                return None
            return data.decode("utf-8")
        except UnicodeError:
            self.diagnostic(DiagnosticCode.METADATA_MALFORMED, "discovery metadata is not valid UTF-8", source=path)
            return None
        except OSError:
            self.diagnostic(DiagnosticCode.PERMISSION_DENIED, "unable to read discovery metadata", source=path)
            return None
        finally:
            if fd >= 0:
                os.close(fd)

    def child_directories(self, directory: Path) -> list[Path] | None:
        """List immediate non-symlink subdirectories, charging entries to the budgets.

        Returns None when the directory cannot be listed in full within the
        deadline and entry limits (a limit diagnostic is emitted).
        """
        if not self._deadline_ok():
            return None
        try:
            resolved = directory.resolve(strict=True)
        except (OSError, RuntimeError):
            self.diagnostic(DiagnosticCode.PERMISSION_DENIED, "unable to resolve discovery directory", source=directory)
            return None
        if not self._within_root(resolved):
            self.diagnostic(
                DiagnosticCode.PATH_OUTSIDE_ROOT, "discovery directory is outside its approved root", source=directory
            )
            return None
        remaining_root = max(0, self.limits.max_entries_per_root - self._root_entries)
        remaining_total = max(0, self.budget.max_entries - self.budget.entries)
        allowance = min(remaining_root, remaining_total)
        try:
            with os.scandir(resolved) as listing:
                entries = list(islice(listing, allowance + 1))
        except OSError:
            self.diagnostic(DiagnosticCode.PERMISSION_DENIED, "unable to inspect discovery directory", source=directory)
            return None
        if len(entries) > allowance:
            if remaining_total <= remaining_root:
                self.limit(DiagnosticCode.COLLECTION_ENTRY_LIMIT_REACHED, "aggregate discovery entry limit reached")
            else:
                self.limit(DiagnosticCode.ITEM_LIMIT_REACHED, "approved root entry limit reached", source=self.root)
            self.halt()
            return None
        self._root_entries += len(entries)
        self.budget.entries += len(entries)
        found: list[Path] = []
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    found.append(Path(entry.path))
            except OSError:
                self.diagnostic(
                    DiagnosticCode.PERMISSION_DENIED, "unable to inspect discovery path", source=Path(entry.path)
                )
        return found

    def files(self, directory: Path, *, name: str | None = None, suffix: str | None = None) -> Iterator[Path]:
        """Yield matching files in stable order, bounded by depth and counters."""
        if self._stopped or not directory.exists():
            return
        try:
            start = directory.resolve(strict=True)
        except (OSError, RuntimeError):
            self.diagnostic(DiagnosticCode.PERMISSION_DENIED, "unable to resolve discovery directory", source=directory)
            return
        if not self._within_root(start):
            self.diagnostic(
                DiagnosticCode.PATH_OUTSIDE_ROOT, "discovery directory is outside its approved root", source=directory
            )
            return

        try:
            initial_depth = len(start.relative_to(self.root).parts)
        except ValueError:
            initial_depth = self.limits.max_depth + 1
        if initial_depth > self.limits.max_depth:
            self.limit(DiagnosticCode.RECURSION_LIMIT_REACHED, "discovery recursion limit reached", source=directory)
            return

        stack: list[tuple[Path, int]] = [(start, initial_depth)]
        depth_limited = False
        while stack and self._deadline_ok():
            current, depth = stack.pop()
            remaining_root = max(0, self.limits.max_entries_per_root - self._root_entries)
            remaining_total = max(0, self.budget.max_entries - self.budget.entries)
            allowance = min(remaining_root, remaining_total)
            try:
                # A directory can contain more entries than the entire budget.
                # Do not load or sort it all before enforcing the cap. If this
                # directory cannot be scanned in full, skip it rather than
                # choosing a filesystem-order-dependent subset.
                with os.scandir(current) as listing:
                    entries = list(islice(listing, allowance + 1))
            except OSError:
                self.diagnostic(
                    DiagnosticCode.PERMISSION_DENIED, "unable to inspect discovery directory", source=current
                )
                continue
            if len(entries) > allowance:
                if remaining_total <= remaining_root:
                    self.limit(DiagnosticCode.COLLECTION_ENTRY_LIMIT_REACHED, "aggregate discovery entry limit reached")
                else:
                    self.limit(DiagnosticCode.ITEM_LIMIT_REACHED, "approved root entry limit reached", source=self.root)
                self.halt()
                break
            entries.sort(key=lambda entry: entry.name.casefold())
            directories: list[Path] = []
            for entry in entries:
                if not self._deadline_ok():
                    break
                if self._root_entries >= self.limits.max_entries_per_root:
                    self.limit(DiagnosticCode.ITEM_LIMIT_REACHED, "approved root entry limit reached", source=self.root)
                    self.halt()
                    break
                if self.budget.entries >= self.budget.max_entries:
                    self.limit(DiagnosticCode.COLLECTION_ENTRY_LIMIT_REACHED, "aggregate discovery entry limit reached")
                    self.halt()
                    break
                self._root_entries += 1
                self.budget.entries += 1
                candidate = Path(entry.path)
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if depth >= self.limits.max_depth:
                            depth_limited = True
                        else:
                            directories.append(candidate)
                        continue
                    if not entry.is_file(follow_symlinks=False) and not entry.is_symlink():
                        continue
                except OSError:
                    self.diagnostic(
                        DiagnosticCode.PERMISSION_DENIED, "unable to inspect discovery path", source=candidate
                    )
                    continue
                if name is not None and entry.name != name:
                    continue
                if suffix is not None and candidate.suffix != suffix:
                    continue
                resolved = self._inspect(candidate)
                if resolved is None or not resolved.is_file():
                    if self._stopped:
                        break
                    continue
                yield candidate
                if self._stopped:
                    break
            stack.extend((item, depth + 1) for item in reversed(directories))
        if depth_limited and not self._stopped:
            self.limit(DiagnosticCode.RECURSION_LIMIT_REACHED, "discovery recursion limit reached", source=directory)
