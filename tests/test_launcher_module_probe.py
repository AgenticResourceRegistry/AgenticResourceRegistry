# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""An importable namespace is not an installed CLI capable of launching workers."""

import subprocess
import sys

from observal_cli.shared import launcher


def test_probe_rejects_namespace_without_required_modules(tmp_path, monkeypatch):
    """Run the actual probe against a namespace-only tree, not a mocked return code."""
    (tmp_path / "observal_cli").mkdir()
    actual_run = subprocess.run
    probes = []

    def run(argv, **kwargs):
        probes.append(argv)
        # Emulate the isolated interpreter's namespace-only installation.
        code = f"import sys; sys.path.insert(0, {str(tmp_path)!r}); " + argv[-1]
        return actual_run([sys.executable, "-I", "-S", "-c", code], **kwargs)

    launcher.importable_in_isolation.cache_clear()
    monkeypatch.setattr(launcher.subprocess, "run", run)
    try:
        assert not launcher.importable_in_isolation()
        assert probes
    finally:
        launcher.importable_in_isolation.cache_clear()


def test_probe_accepts_installation_with_required_modules(tmp_path, monkeypatch):
    """A minimal real package with both required entry points satisfies the probe."""
    package = tmp_path / "observal_cli"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "hook_gate.py").write_text("")
    (package / "hooks").mkdir()
    (package / "hooks/__init__.py").write_text("")
    (package / "hooks/session_push.py").write_text("")
    actual_run = subprocess.run

    def run(argv, **kwargs):
        code = f"import sys; sys.path.insert(0, {str(tmp_path)!r}); " + argv[-1]
        return actual_run([sys.executable, "-I", "-S", "-c", code], **kwargs)

    launcher.importable_in_isolation.cache_clear()
    monkeypatch.setattr(launcher.subprocess, "run", run)
    try:
        assert launcher.importable_in_isolation()
    finally:
        launcher.importable_in_isolation.cache_clear()
