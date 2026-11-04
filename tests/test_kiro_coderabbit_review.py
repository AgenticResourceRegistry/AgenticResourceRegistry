# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression controls for confirmed #2033 review findings."""

import json
from pathlib import Path

import pytest

from observal_cli import cmd_doctor, telemetry_buffer
from observal_cli.harness import SessionSource, ensure_loaded
from observal_cli.harness_specs.kiro_hooks_spec import build_kiro_hooks_file
from observal_cli.sessions import base, delivery_guard


def test_prompt_fingerprint_does_not_depend_on_locale(tmp_path, monkeypatch):
    """UTF-8 prompt bytes must remain readable under a cp1252 text default."""
    path = tmp_path / "messages.jsonl"
    line = json.dumps({"payload": {"type": "user", "content": "Á"}}, ensure_ascii=False) + "\n"
    path.write_bytes(line.encode("utf-8"))
    source = SessionSource("kiro", "session", path)
    expected = delivery_guard._prompt_fingerprint(source)
    assert expected is not None
    original_open = Path.open

    def locale_open(self, mode="r", *args, **kwargs):
        """Simulate the platform text default while leaving binary reads unchanged."""
        if self == path and "b" not in mode and "encoding" not in kwargs:
            kwargs["encoding"] = "cp1252"
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", locale_open)
    assert delivery_guard._prompt_fingerprint(source) == expected


def test_unknown_outbox_harness_does_not_block_registered_batches(tmp_path, monkeypatch):
    """A removed harness is quarantined without dropping the following valid batch."""
    db = tmp_path / "outbox.db"
    config = {"server_url": "http://peer", "user_id": "user"}
    for harness, sid in [("removed-harness", "old"), ("claude-code", "new")]:
        telemetry_buffer.enqueue(
            {"harness": harness, "session_id": sid, "start_offset": 0, "lines": ["{}"], "end_byte_offsets": [3]},
            destination=config["server_url"],
            user_id=config["user_id"],
            db_path=db,
        )
    posts, quarantines = [], []
    quarantine = telemetry_buffer.quarantine

    def record_quarantine(item, reason, **kwargs):
        """Record permanent rejection and retain the normal durable quarantine behavior."""
        quarantines.append((item.harness, reason))
        return quarantine(item, reason, **kwargs)

    monkeypatch.setattr(telemetry_buffer, "quarantine", record_quarantine)
    assert base.drain_outbox(
        config,
        db_path=db,
        home=tmp_path,
        post=lambda payload, cfg: posts.append(payload) or {"acknowledged_line": 0, "acknowledged_offset": 3},
    )
    assert [payload["session_id"] for payload in posts] == ["new"]
    assert quarantines[0][0] == "removed-harness"
    assert not telemetry_buffer.pending(destination=config["server_url"], user_id="user", db_path=db)


@pytest.mark.parametrize("engine", ["v2", "v3"])
def test_doctor_warns_for_unusable_standalone_without_agents(tmp_path, monkeypatch, engine):
    """A configured surface lacking complete hook coverage cannot silently look healthy."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("KIRO_HOME", raising=False)
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", engine)
    monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "2.35.0")
    monkeypatch.setenv("OBSERVAL_KIRO_IDE", "0")
    root = tmp_path / ".kiro"
    (root / "hooks").mkdir(parents=True)
    data = build_kiro_hooks_file()
    if engine == "v3":
        data["hooks"] = data["hooks"][:1]
    (root / "hooks/observal.json").write_text(json.dumps(data))
    ensure_loaded()
    warnings = []
    cmd_doctor._check_kiro([], warnings)
    assert any("Kiro" in warning and "doctor patch --harness kiro" in warning for warning in warnings)


def test_doctor_lifecycle_uses_custom_runtime_root(tmp_path, monkeypatch):
    """Patch, health and cleanup agree with discovery without touching the default root."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    custom = tmp_path / "relocated-kiro"
    monkeypatch.setenv("KIRO_HOME", str(custom))
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", "v3")
    monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "2.35.0")
    monkeypatch.setenv("OBSERVAL_KIRO_IDE", "0")
    monkeypatch.setattr("observal_cli.lockfile.read_registry_lockfile", lambda: ("server", {}))
    (custom / "agents").mkdir(parents=True)
    (custom / "hooks").mkdir()
    user = {"name": "user-kept", "trigger": "Stop", "action": {"type": "command", "command": "echo kept"}}
    config = build_kiro_hooks_file()
    config["hooks"] = [user]
    file = custom / "hooks/observal.json"
    file.write_text(json.dumps(config))
    default_file = tmp_path / ".kiro/hooks/observal.json"
    default_file.parent.mkdir(parents=True)
    default_file.write_text('{"default_kept":true}')
    original = default_file.read_bytes()
    ensure_loaded()
    assert cmd_doctor._patch_kiro(False)
    assert len(json.loads(file.read_text())["hooks"]) == 3
    warnings = []
    cmd_doctor._check_kiro([], warnings)
    assert not warnings
    assert cmd_doctor._cleanup_kiro(False)
    assert json.loads(file.read_text())["hooks"] == [user]
    assert default_file.read_bytes() == original
