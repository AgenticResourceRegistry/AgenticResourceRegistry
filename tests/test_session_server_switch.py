# SPDX-License-Identifier: Apache-2.0
"""Session delivery follows the server the CLI is configured for, including environment overrides."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from observal_cli import config as cli_config
from observal_cli import telemetry_buffer
from observal_cli.harness import SessionSource
from observal_cli.hooks import session_push
from observal_cli.sessions import base

SERVER_A = "https://a.example"
SERVER_B = "https://b.example"
ENV_NAMES = (
    "OBSERVAL_SERVER_URL",
    "OBSERVAL_ACCESS_TOKEN",
    "OBSERVAL_ACCESS_TOKEN_FILE",
    "OBSERVAL_API_KEY",
    "OBSERVAL_API_KEY_FILE",
    "OBSERVAL_TOKEN",
    "OBSERVAL_TOKEN_FILE",
)


def jwt(subject: str) -> str:
    def segment(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{segment({'alg': 'ES256'})}.{segment({'sub': subject, 'type': 'access'})}.signature"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def save_login(home: Path, server: str = SERVER_A, user: str = "user-a") -> None:
    directory = home / ".observal"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.json").write_text(
        json.dumps({"server_url": server, "access_token": "token-a", "refresh_token": "refresh-a", "user_id": user})
    )


# ── Hooks resolve the server and token like every other command ─────────────


def test_environment_overrides_the_saved_login(tmp_path, monkeypatch):
    save_login(tmp_path)
    token_b = jwt("user-b")
    monkeypatch.setenv("OBSERVAL_SERVER_URL", SERVER_B)
    monkeypatch.setenv("OBSERVAL_ACCESS_TOKEN", token_b)

    config = base.load_config(tmp_path)

    assert config["server_url"] == SERVER_B
    assert config["access_token"] == token_b
    assert config["user_id"] == "user-b"  # outbox batches are filed under the token's user
    assert config["refresh_token"] == ""  # server A's refresh token is never sent to server B


def test_environment_alone_configures_delivery_without_a_saved_login(tmp_path, monkeypatch):
    token_file = tmp_path / "token"
    token_file.write_text(jwt("ci-user") + "\n")
    monkeypatch.setenv("OBSERVAL_SERVER_URL", SERVER_B)
    monkeypatch.setenv("OBSERVAL_ACCESS_TOKEN_FILE", str(token_file))

    config = base.load_config(tmp_path / "fresh-home")

    assert (config["server_url"], config["user_id"], config["refresh_token"]) == (SERVER_B, "ci-user", "")
    assert config["access_token"] == jwt("ci-user")


def test_server_override_to_the_saved_server_keeps_refreshing(tmp_path, monkeypatch):
    save_login(tmp_path)
    monkeypatch.setenv("OBSERVAL_SERVER_URL", SERVER_A + "/")

    config = base.load_config(tmp_path)

    assert (config["server_url"], config["refresh_token"], config["user_id"]) == (SERVER_A + "/", "refresh-a", "user-a")


def test_server_override_elsewhere_never_refreshes_the_saved_login_there(tmp_path, monkeypatch):
    save_login(tmp_path)
    monkeypatch.setenv("OBSERVAL_SERVER_URL", SERVER_B)

    config = base.load_config(tmp_path)

    assert config["server_url"] == SERVER_B
    assert config["refresh_token"] == ""


def test_ambiguous_token_environment_skips_delivery_quietly(tmp_path, monkeypatch):
    save_login(tmp_path)
    monkeypatch.setenv("OBSERVAL_ACCESS_TOKEN", "direct")
    monkeypatch.setenv("OBSERVAL_ACCESS_TOKEN_FILE", str(tmp_path / "token"))

    assert base.load_config(tmp_path) is None


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"OBSERVAL_SERVER_URL": SERVER_B},
        {"OBSERVAL_ACCESS_TOKEN": "access"},
        {"OBSERVAL_SERVER_URL": SERVER_B, "OBSERVAL_API_KEY": "api-key", "OBSERVAL_ACCESS_TOKEN": "access"},
        {"OBSERVAL_TOKEN": "legacy", "OBSERVAL_API_KEY": "api-key"},
    ],
)
def test_hooks_and_commands_resolve_the_same_server_and_token(tmp_path, monkeypatch, environment):
    save_login(tmp_path)
    monkeypatch.setattr(cli_config, "CONFIG_FILE", tmp_path / ".observal" / "config.json")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    command = cli_config.load()
    hook = base.load_config(tmp_path)

    assert (hook["server_url"], hook["access_token"]) == (command["server_url"], command["access_token"])


# ── Delivery cursors are kept per server ──────────────────────────────────


def test_cursors_are_independent_per_server(tmp_path):
    base.write_cursor("session", 40, 4, finalized=True, home=tmp_path, destination=SERVER_A)

    assert base.read_cursor_state("session", home=tmp_path, destination=SERVER_B) == (0, 0, False)
    base.write_cursor("session", 10, 1, home=tmp_path, destination=SERVER_B + "/")

    assert base.read_cursor_state("session", home=tmp_path, destination=SERVER_A) == (40, 4, True)
    assert base.read_cursor_state("session", home=tmp_path, destination=SERVER_B) == (10, 1, False)
    state = json.loads((tmp_path / ".observal" / "sync_state.json").read_text())
    assert state["version"] == 2
    assert set(state["servers"]) == {SERVER_A, SERVER_B}
    assert base.delivered_to_another_server("session", home=tmp_path, destination="https://c.example")
    assert base.highest_delivered_offset("session", home=tmp_path) == 40


def test_single_server_state_belongs_to_the_saved_login(tmp_path):
    save_login(tmp_path)
    (tmp_path / ".observal" / "sync_state.json").write_text(
        json.dumps({"old-session": {"offset": 12, "line_count": 2, "finalized": True}})
    )

    assert base.read_cursor_state("old-session", home=tmp_path, destination=SERVER_A) == (12, 2, True)
    assert base.read_cursor_state("old-session", home=tmp_path, destination=SERVER_B) == (0, 0, False)

    base.write_cursor("new-session", 5, 1, home=tmp_path, destination=SERVER_B)
    save_login(tmp_path, server=SERVER_B, user="user-b")  # a later login does not reassign the old entries

    state = json.loads((tmp_path / ".observal" / "sync_state.json").read_text())
    assert state["legacy_server"] == SERVER_A
    assert state["servers"] == {SERVER_B: {"new-session": {"offset": 5, "line_count": 1}}}
    assert base.read_cursor_state("old-session", home=tmp_path, destination=SERVER_A) == (12, 2, True)
    assert base.read_cursor_state("old-session", home=tmp_path, destination=SERVER_B) == (0, 0, False)
    assert base.delivered_to_another_server("old-session", home=tmp_path, destination=SERVER_B)


def test_top_level_entries_written_by_the_pi_extension_survive_and_still_count(tmp_path):
    save_login(tmp_path)
    state_file = tmp_path / ".observal" / "sync_state.json"
    base.write_cursor("python-session", 8, 1, home=tmp_path, destination=SERVER_A)
    # The Pi extension adds its own cursor at the top level, as it always has.
    state = json.loads(state_file.read_text())
    state["pi-session"] = {"offset": 30, "line_count": 3, "finalized": True, "last_pushed_at": 1}
    state_file.write_text(json.dumps(state))

    base.write_cursor("python-session", 16, 2, home=tmp_path, destination=SERVER_A)

    state = json.loads(state_file.read_text())
    assert state["pi-session"] == {"offset": 30, "line_count": 3, "finalized": True, "last_pushed_at": 1}
    assert base.read_cursor_state("pi-session", home=tmp_path, destination=SERVER_A) == (30, 3, True)
    assert base.read_cursor_state("python-session", home=tmp_path, destination=SERVER_A) == (16, 2, False)


def _post_all(received: list[dict]):
    def post(payload: dict, _config: dict) -> dict:
        received.append(payload)
        if not payload["lines"]:
            return {"acknowledged_line": payload["start_offset"] - 1}
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload["end_byte_offsets"][-1],
        }

    return post


def test_a_session_delivered_to_one_server_is_delivered_whole_to_the_next(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "_resolve_agent", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(base, "_get_cached_layer_hash", lambda *_args, **_kwargs: None)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"n":0}\n{"n":1}\n')
    source = SessionSource("claude-code", "session", transcript)
    db = tmp_path / "outbox.db"
    to_a: list[dict] = []
    to_b: list[dict] = []

    for server, received in ((SERVER_A, to_a), (SERVER_B, to_b)):
        assert base.drain_session_source(
            source,
            {"server_url": server, "access_token": "token", "user_id": "user"},
            hook_event="Stop",
            final=True,
            home=tmp_path,
            db_path=db,
            post=_post_all(received),
        )

    assert [line for payload in to_a for line in payload["lines"]] == ['{"n":0}', '{"n":1}']
    assert [line for payload in to_b for line in payload["lines"]] == ['{"n":0}', '{"n":1}']
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0
    for server in (SERVER_A, SERVER_B):
        assert base.read_cursor_state("session", home=tmp_path, destination=server) == (
            transcript.stat().st_size,
            2,
            True,
        )


# ── Recovery after a switch ───────────────────────────────────────────────


class _Adapter:
    def __init__(self, sources):
        self.sources = sources

    def aged_recovery_final(self):
        return True

    def discover_session_sources(self, home=None, since_hours=168):
        return self.sources

    def related_session_sources(self, source, home=None):
        return []

    def should_capture_session(self, source, home=None):
        return True

    def session_extra_fields(self, source, event, final, home=None):
        return {}

    def session_extra_records(self, source, event, final, home=None):
        return ()

    def is_installed(self):
        return True


def _aged_transcript(path: Path) -> Path:
    import os
    import time

    path.write_text('{"n":0}\n')
    old = time.time() - 3600
    os.utime(path, (old, old))
    return path


def test_background_recovery_leaves_sessions_delivered_before_a_switch_alone(tmp_path, monkeypatch):
    delivered_to_a = _aged_transcript(tmp_path / "delivered.jsonl")
    never_delivered = _aged_transcript(tmp_path / "missed.jsonl")
    base.write_cursor(
        "delivered", delivered_to_a.stat().st_size, 1, finalized=True, home=tmp_path, destination=SERVER_A
    )
    adapter = _Adapter(
        [
            SessionSource("claude-code", "delivered", delivered_to_a),
            SessionSource("claude-code", "missed", never_delivered),
        ]
    )
    drained: list[str] = []
    monkeypatch.setattr(session_push, "ensure_loaded", lambda: None)
    monkeypatch.setattr(session_push, "get_adapter", lambda _harness: adapter)
    monkeypatch.setattr(
        session_push, "load_config", lambda home=None: {"server_url": SERVER_B, "user_id": "user", "access_token": "t"}
    )
    monkeypatch.setattr(session_push, "drain_outbox", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        session_push, "drain_session_source", lambda source, *_args, **_kwargs: drained.append(source.session_id)
    )

    session_push._recover_sessions("claude-code", home=tmp_path)

    assert drained == ["missed"]  # a hook never copies past work to a newly configured server


def test_reconcile_backfills_sessions_delivered_to_the_previous_server(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from observal_cli import cmd_reconcile_cli
    from observal_cli.main import app

    transcript = _aged_transcript(tmp_path / "session.jsonl")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    base.write_cursor("session-1", transcript.stat().st_size, 1, finalized=True, destination=SERVER_A)
    source = SessionSource("kiro", "session-1", transcript)
    adapter = _Adapter([source])
    drained: list[tuple[str, str]] = []

    monkeypatch.setattr(
        cmd_reconcile_cli, "load_config", lambda: {"server_url": SERVER_B, "user_id": "user", "access_token": "t"}
    )
    monkeypatch.setattr(cmd_reconcile_cli, "ensure_loaded", lambda: None)
    monkeypatch.setattr(cmd_reconcile_cli, "get_all_adapters", lambda: {"kiro": adapter})
    monkeypatch.setattr(cmd_reconcile_cli, "get_adapter", lambda _name: adapter)
    monkeypatch.setattr(cmd_reconcile_cli, "drain_outbox", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cmd_reconcile_cli, "recover_cursor_from_server", lambda _source, _config: (0, 0))

    def drain(source, config, **_kwargs):
        drained.append((source.session_id, config["server_url"]))
        return True

    monkeypatch.setattr(cmd_reconcile_cli, "drain_session_source", drain)

    result = CliRunner().invoke(app, ["reconcile", "--harness", "kiro", "--since", "3", "--output", "json"])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)["summary"]
    assert summary["up_to_date"] == 0
    assert drained == [("session-1", SERVER_B)]
