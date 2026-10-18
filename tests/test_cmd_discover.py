# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""observal discover, the capability lock, session attribution and agent init --from-capabilities."""

from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from typer.testing import CliRunner

import observal_cli.cmd_agent as agent
import observal_cli.cmd_discover as discover
from observal_cli import capability_lock
from observal_cli.sessions import base as sessions_base

runner = CliRunner()

URN = "urn:air:observal.example.com:skill:0f3c4d5e-6a7b-4c8d-9e0f-1a2b3c4d5e6f"
ENTITY = "0f3c4d5e-6a7b-4c8d-9e0f-1a2b3c4d5e6f"
MCP_URN = "urn:air:observal.example.com:mcp:9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4c5d"


def _result(**overrides):
    base = {
        "identifier": URN,
        "displayName": "Security Review",
        "type": "application/ai-skill+md",
        "url": f"https://observal.example.com/api/v1/artifacts/skill/{ENTITY}/1.2.0",
        "version": "1.2.0",
        "description": "Reviews pull requests for auth bugs.",
        "score": 94,
        "source": "https://observal.example.com/api/v1/ard",
        "matchedOn": ["review", "auth"],
        "obs:kind": "skill",
        "obs:nativeRef": "acme/security-review@1.2.0",
        "obs:approval": "approved",
        "obs:availability": "now",
        "obs:supportedHarnesses": ["claude-code", "kiro", "pi"],
        "obs:activatable": True,
        "obs:artifactDigest": "sha256:abc",
    }
    base.update(overrides)
    return base


def _entry(**overrides):
    base = {
        "identifier": URN,
        "displayName": "Security Review",
        "type": "application/ai-skill+md",
        "url": f"https://observal.example.com/api/v1/artifacts/skill/{ENTITY}/1.2.0",
        "version": "1.2.0",
        "description": "Reviews pull requests for auth bugs.",
        "capabilities": ["code review", "security"],
        "representativeQueries": ["review this pull request for auth bugs", "find authentication issues"],
        "obs:kind": "skill",
        "obs:nativeRef": "acme/security-review@1.2.0",
        "obs:lifecycle": "approved",
        "obs:visibility": "public",
        "obs:supportedHarnesses": ["claude-code", "kiro", "pi"],
        "obs:activatable": True,
        "obs:artifactDigest": "sha256:abc",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _quiet(monkeypatch, tmp_path):
    monkeypatch.setattr(discover, "spinner", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(capability_lock, "LOCK_PATH", tmp_path / "capability_lock.jsonl")
    monkeypatch.delenv("OBSERVAL_HARNESS", raising=False)
    monkeypatch.delenv("OBSERVAL_SESSION_ID", raising=False)
    return tmp_path


def _invoke(*args, input=None):
    return runner.invoke(discover.discover_app, list(args), input=input)


# ── search ───────────────────────────────────────────────────────────────


def test_search_posts_ard_request_and_prints_table(monkeypatch):
    post = Mock(return_value={"results": [_result()]})
    monkeypatch.setattr(discover.client, "post", post)

    result = _invoke("search", "review", "a", "pull", "request", "--type", "skill", "--harness", "kiro")

    assert result.exit_code == 0, result.output
    path, body = post.call_args.args[0], post.call_args.args[1]
    assert path == "/api/v1/ard/search"
    assert body["query"]["text"] == "review a pull request"
    assert body["query"]["filter"] == {
        "type": ["application/ai-skill+md"],
        "obs:supportedHarnesses": ["kiro"],
        "obs:lifecycle": ["approved"],
    }
    assert body["federation"] == "none"
    # Rich wraps table cells in a narrow test terminal, so check tokens, not phrases.
    assert "Security" in result.output and "acme/security" in result.output
    assert "observal discover use" in result.output


def test_search_json_output_is_deterministic(monkeypatch):
    monkeypatch.setattr(discover.client, "post", Mock(return_value={"results": [_result()]}))
    result = _invoke("search", "auth", "review", "--output", "json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["query"] == "auth review"
    assert data["count"] == 1
    assert data["results"][0]["identifier"] == URN


def test_search_include_unapproved_drops_lifecycle_filter(monkeypatch):
    post = Mock(return_value={"results": []})
    monkeypatch.setattr(discover.client, "post", post)
    result = _invoke("search", "anything", "--include-unapproved")
    assert result.exit_code == 0
    assert "obs:lifecycle" not in post.call_args.args[1]["query"]["filter"]
    assert "Nothing in Observal matches" in result.output


def test_empty_search_says_discovery_is_scoped_to_your_teamspaces(monkeypatch):
    monkeypatch.setattr(discover.client, "post", Mock(return_value={"results": []}))
    result = _invoke("search", "anything")
    assert result.exit_code == 0, result.output
    assert "teamspaces you belong to" in " ".join(result.output.split())
    assert "observal registry <type> list" in " ".join(result.output.split())
    assert "observal agent list" in " ".join(result.output.split())


def test_search_rejects_unknown_type_and_harness(monkeypatch):
    monkeypatch.setattr(discover.client, "post", Mock(return_value={"results": []}))
    bad_type = _invoke("search", "x", "--type", "spaceship")
    assert bad_type.exit_code == 7
    assert "Unknown resource type" in bad_type.output
    bad_harness = _invoke("search", "x", "--harness", "notepad")
    assert bad_harness.exit_code == 7
    assert "Unknown harness" in bad_harness.output


# ── inspect ──────────────────────────────────────────────────────────────


def test_inspect_fetches_entry_and_accepts_legacy_prefix(monkeypatch):
    get = Mock(return_value=_entry())
    monkeypatch.setattr(discover.client, "get", get)
    result = _invoke("inspect", URN.replace("urn:air:", "urn:ai:"))
    assert result.exit_code == 0, result.output
    assert get.call_args.args[0] == f"/api/v1/ard/entries/{URN}"
    assert "Security Review" in result.output and "Good for" in result.output


def test_inspect_rejects_non_urn():
    result = _invoke("inspect", "acme/security-review")
    assert result.exit_code == 7
    assert "Not an Observal resource identifier" in result.output


def test_remote_a2a_agents_are_inspected_and_pointed_at_delegate(monkeypatch):
    a2a = "urn:air:agents.acme.com:a2a:incident-triage"
    entry = _entry(identifier=a2a, **{"obs:kind": "external", "obs:protocol": "a2a"})  # as GET /ard/entries returns it
    monkeypatch.setattr(discover.client, "get", Mock(return_value=entry))
    result = _invoke("inspect", a2a)
    assert result.exit_code == 0, result.output
    assert f"observal delegate run {a2a}" in result.output

    result = _invoke("use", a2a, "--output", "json")
    assert result.exit_code == 7
    assert "delegate run" in result.output


# ── use ──────────────────────────────────────────────────────────────────


def test_use_skill_prints_content_and_records_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(discover.client, "get", Mock(return_value=_entry()))
    monkeypatch.setattr(discover.client, "get_text", Mock(return_value="# Security Review\nLook for auth bugs."))
    monkeypatch.setenv("OBSERVAL_HARNESS", "kiro")

    result = _invoke("use", URN, "--output", "json")

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["activated"] is True and data["mode"] == "context"
    assert data["harness"] == "kiro" and data["content"].startswith("# Security Review")
    assert data["digest"] == "sha256:abc"
    uses = capability_lock.read_all()
    assert len(uses) == 1
    use = uses[0]
    assert (use.identifier, use.kind, use.component_id, use.version) == (URN, "skill", ENTITY, "1.2.0")
    assert use.harness == "kiro" and use.mode == "context" and use.source == "discover-cli"
    assert use.cwd == str(Path.cwd().resolve())


def test_use_truncates_long_content(monkeypatch):
    monkeypatch.setattr(discover.client, "get", Mock(return_value=_entry()))
    monkeypatch.setattr(discover.client, "get_text", Mock(return_value="x" * 5000))
    result = _invoke("use", URN, "--max-chars", "1000", "--output", "json")
    data = json.loads(result.output)
    assert data["truncated"] is True and len(data["content"]) == 1000


def test_use_refuses_unapproved_without_yes(monkeypatch):
    monkeypatch.setattr(discover.client, "get", Mock(return_value=_entry(**{"obs:lifecycle": "pending"})))
    monkeypatch.setattr(discover.client, "get_text", Mock(return_value="body"))
    refused = _invoke("use", URN)
    assert refused.exit_code == 4
    assert "not approved" in refused.output
    assert capability_lock.read_all() == []
    allowed = _invoke("use", URN, "--yes", "--output", "json")
    assert allowed.exit_code == 0, allowed.output


def test_use_refuses_unsupported_harness(monkeypatch):
    monkeypatch.setattr(discover.client, "get", Mock(return_value=_entry()))
    result = _invoke("use", URN, "--harness", "goose")
    assert result.exit_code == 7
    assert "does not list goose" in result.output


def test_use_mcp_points_at_install_command_and_records_nothing(monkeypatch):
    entry = _entry(
        identifier=MCP_URN,
        displayName="GitHub",
        type="application/mcp-server-card+json",
        **{"obs:kind": "mcp", "obs:nativeRef": "acme/github@1.4.2", "obs:supportedHarnesses": ["kiro"]},
    )
    monkeypatch.setattr(discover.client, "get", Mock(return_value=entry))
    result = _invoke("use", MCP_URN, "--harness", "kiro", "--output", "json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["activated"] is False and data["mode"] == "next-session"
    # The install command pins the version the entry described.
    assert data["next_step"] == "observal registry mcp install acme/github --harness kiro --version 1.4.2"
    assert capability_lock.read_all() == []


def test_next_step_quotes_registry_references_for_the_shell():
    hostile = {"obs:kind": "skill", "obs:nativeRef": "acme/x;touch pwned@1.0.0 $(id)"}

    step = discover._next_step(hostile, "kiro")

    assert step == "observal registry skill install 'acme/x;touch pwned' --harness kiro --version '1.0.0 $(id)'"


def test_use_prompt_renders_json_artifact(monkeypatch):
    entry = _entry(
        identifier=URN.replace(":skill:", ":prompt:"),
        type="application/vnd.observal.prompt+json",
        **{"obs:kind": "prompt", "obs:nativeRef": "acme/release-notes@0.3.0"},
    )
    get = Mock(side_effect=[entry, {"template": "Write release notes for: {{changes}}"}])
    monkeypatch.setattr(discover.client, "get", get)
    result = _invoke("use", URN.replace(":skill:", ":prompt:"), "--output", "json")
    assert result.exit_code == 0, result.output
    assert "Write release notes" in json.loads(result.output)["content"]
    assert get.call_args.args[0] == f"/api/v1/artifacts/prompt/{ENTITY}/1.2.0"


def test_use_table_output_strips_terminal_control_sequences(monkeypatch):
    hostile = "# Skill\x1b]0;owned\x07 body \x1b[31mred\x1b[0m \x1b[2J\x07 tail\ttab\n"
    monkeypatch.setattr(discover.client, "get", Mock(return_value=_entry()))
    monkeypatch.setattr(discover.client, "get_text", Mock(return_value=hostile))
    result = _invoke("use", URN, "--harness", "pi")
    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.output and "\x07" not in result.output
    assert "# Skill body red  tail\ttab" in result.output
    # JSON output is a data channel and stays byte-exact.
    result = _invoke("use", URN, "--harness", "pi", "--output", "json")
    assert json.loads(result.output)["content"] == hostile


def test_sanitize_for_terminal_keeps_plain_text():
    text = "line one\n\tindented — unicode ✓ and [brackets]\r\n"
    assert discover.sanitize_for_terminal(text) == text


# ── capability lock ──────────────────────────────────────────────────────


def test_lock_record_read_match_and_prune(tmp_path):
    path = tmp_path / "lock.jsonl"
    now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    capability_lock.record(
        kind="skill",
        mode="context",
        source="discover-cli",
        harness="kiro",
        cwd=tmp_path / "repo",
        identifier=URN,
        component_id=ENTITY,
        version="1.2.0",
        digest="sha256:abc",
        path=path,
        now=now,
    )
    capability_lock.record(
        kind="mcp",
        mode="next-session",
        source="install",
        harness="kiro",
        cwd=tmp_path / "repo" / "sub",
        component_id="m-1",
        native_ref="acme/github@1.4.2",
        version="1.4.2",
        path=path,
        now=now + timedelta(minutes=5),
    )
    capability_lock.record(
        kind="skill",
        mode="context",
        source="discover-cli",
        harness="pi",
        cwd=tmp_path / "other",
        identifier="urn:air:x:skill:other",
        path=path,
        now=now - timedelta(days=40),
    )

    assert len(capability_lock.read_all(path)) == 3

    matched = capability_lock.matching(
        harness="kiro", cwd=str(tmp_path / "repo"), since=now - timedelta(minutes=1), path=path
    )
    assert [u.kind for u in matched] == ["skill", "mcp"], "sub-directory uses belong to the parent session"

    assert capability_lock.matching(harness="pi", cwd=str(tmp_path / "repo"), since=None, path=path) == []

    payload = capability_lock.to_payload(matched, confidence="window")
    assert payload[0]["identifier"] == URN and payload[0]["confidence"] == "window"
    assert payload[1]["native_ref"] == "acme/github@1.4.2"

    removed = capability_lock.prune(retention_days=30, path=path, now=now + timedelta(minutes=10))
    assert removed == 1 and len(capability_lock.read_all(path)) == 2


def test_lock_session_hint_matches_regardless_of_window(tmp_path):
    path = tmp_path / "lock.jsonl"
    old = datetime(2026, 1, 1, tzinfo=UTC)
    capability_lock.record(
        kind="skill",
        mode="context",
        source="discover-cli",
        harness="claude-code",
        cwd=tmp_path,
        identifier=URN,
        session_hint="sess-1",
        path=path,
        now=old,
    )
    hit = capability_lock.matching(
        harness="pi", cwd="/elsewhere", since=datetime.now(UTC), session_hint="sess-1", path=path
    )
    assert len(hit) == 1
    assert capability_lock.to_payload(hit, confidence="window")[0]["confidence"] == "exact"


def test_lock_dedupes_to_latest_use(tmp_path):
    path = tmp_path / "lock.jsonl"
    now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    for minute, version in ((0, "1.0.0"), (5, "1.1.0")):
        capability_lock.record(
            kind="skill",
            mode="context",
            source="discover-cli",
            harness="kiro",
            cwd=tmp_path,
            identifier=URN,
            version=version,
            path=path,
            now=now + timedelta(minutes=minute),
        )
    latest = capability_lock.dedupe_latest(capability_lock.read_all(path))
    assert [u.version for u in latest] == ["1.1.0"]


def test_lock_ignores_corrupt_lines_and_rejects_bad_mode(tmp_path):
    path = tmp_path / "lock.jsonl"
    path.write_text('not json\n{"ts": "2026-01-01T00:00:00Z"}\n', encoding="utf-8")
    assert capability_lock.read_all(path) == []
    with pytest.raises(ValueError):
        capability_lock.record(kind="skill", mode="teleport", source="x", harness=None, path=path)


# ── session attribution ──────────────────────────────────────────────────


def test_build_payload_attaches_matching_capabilities(monkeypatch, tmp_path):
    monkeypatch.setattr(sessions_base, "_resolve_agent", lambda *_a, **_k: (None, None))
    monkeypatch.setattr(sessions_base, "_get_cached_layer_hash", lambda *_a, **_k: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    capability_lock.record(
        kind="skill",
        mode="context",
        source="discover-cli",
        harness="kiro",
        cwd=repo,
        identifier=URN,
        component_id=ENTITY,
        version="1.2.0",
        digest="sha256:abc",
    )
    capability_lock.record(
        kind="skill",
        mode="context",
        source="discover-cli",
        harness="cursor",
        cwd=repo,
        identifier="urn:air:x:skill:no",
    )

    payload = sessions_base.build_payload(
        session_id="s-1",
        lines=["{}"],
        start_offset=0,
        hook_event="Stop",
        line_count_before=0,
        cwd=str(repo),
        session_jsonl=transcript,
        harness="kiro",
    )

    used = payload["capabilities_used"]
    assert len(used) == 1
    assert used[0]["identifier"] == URN and used[0]["confidence"] == "window" and used[0]["mode"] == "context"


def test_session_start_prefers_birthtime_then_first_line_never_ctime(monkeypatch, tmp_path):
    transcript = tmp_path / "s.jsonl"
    transcript.write_text('{"type": "session", "timestamp": "2026-09-20T10:00:00Z"}\n{"x": 1}\n', encoding="utf-8")

    class _Stat:
        st_birthtime = 0  # platform without birth time
        st_ctime = datetime.now(UTC).timestamp()  # would be "now" on Linux after writes

    monkeypatch.setattr(type(transcript), "stat", lambda self: _Stat())
    started = sessions_base._session_started_at(transcript)
    assert started == datetime(2026, 9, 20, 9, 45, tzinfo=UTC), "first-line timestamp minus the lead, not ctime"

    (tmp_path / "no-ts.jsonl").write_text("not json\n", encoding="utf-8")
    fallback = sessions_base._session_started_at(tmp_path / "no-ts.jsonl")
    assert datetime.now(UTC) - fallback > timedelta(hours=23), "no signal: wide fallback window"


def test_build_payload_caps_capabilities_to_the_ingest_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(sessions_base, "_resolve_agent", lambda *_a, **_k: (None, None))
    monkeypatch.setattr(sessions_base, "_get_cached_layer_hash", lambda *_a, **_k: None)
    now = datetime.now(UTC)
    for i in range(250):
        capability_lock.record(
            kind="skill",
            mode="context",
            source="discover-cli",
            harness="kiro",
            cwd=tmp_path,
            identifier=f"urn:air:x:skill:{i:04d}",
            version="1.0.0",
            now=now - timedelta(seconds=250 - i),
        )
    payload = sessions_base.build_payload(
        session_id="s-cap",
        lines=[],
        start_offset=0,
        hook_event="Stop",
        line_count_before=0,
        cwd=str(tmp_path),
        harness="kiro",
    )
    used = payload["capabilities_used"]
    assert len(used) == 200
    assert used[0]["identifier"] == "urn:air:x:skill:0249", "most recent uses are kept"


def test_build_payload_omits_field_when_nothing_matches(monkeypatch, tmp_path):
    monkeypatch.setattr(sessions_base, "_resolve_agent", lambda *_a, **_k: (None, None))
    monkeypatch.setattr(sessions_base, "_get_cached_layer_hash", lambda *_a, **_k: None)
    payload = sessions_base.build_payload(
        session_id="s-2",
        lines=[],
        start_offset=0,
        hook_event="UserPromptSubmit",
        line_count_before=0,
        cwd=str(tmp_path),
        harness="kiro",
    )
    assert "capabilities_used" not in payload


# ── agent init --from-capabilities ───────────────────────────────────────


@pytest.fixture()
def _agent_boundaries(monkeypatch):
    monkeypatch.setattr(agent, "spinner", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(agent.config, "load", Mock(return_value={"username": "alice"}))
    return SimpleNamespace()


def test_agent_init_from_capabilities_prefills_components(monkeypatch, tmp_path, _agent_boundaries):
    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    capability_lock.record(
        kind="skill",
        mode="context",
        source="discover-cli",
        harness="kiro",
        cwd=tmp_path,
        identifier=URN,
        component_id=ENTITY,
        native_ref="acme/security-review@1.2.0",
        version="1.2.0",
        now=now,
    )
    capability_lock.record(
        kind="mcp",
        mode="next-session",
        source="install",
        harness="claude-code",
        cwd=tmp_path,
        component_id="9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4c5d",
        native_ref="acme/github@1.4.2",
        version="1.4.2",
        now=now,
    )
    capability_lock.record(
        kind="agent",
        mode="next-session",
        source="pull",
        harness="kiro",
        cwd=tmp_path,
        component_id="aaaaaaaa-0000-4000-8000-000000000000",
        native_ref="acme/pr-reviewer@3.1.0",
        version="3.1.0",
        now=now,
    )
    capability_lock.record(  # too old for the window
        kind="prompt",
        mode="context",
        source="discover-cli",
        harness="kiro",
        cwd=tmp_path,
        component_id="bbbbbbbb-0000-4000-8000-000000000000",
        now=now - timedelta(days=3),
    )

    result = runner.invoke(
        agent.agent_app,
        ["init", "--dir", str(tmp_path / "out"), "--from-capabilities", "--name", "pr-review-flow", "--output", "json"],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    saved = yaml.safe_load((tmp_path / "out" / agent.YAML_FILE).read_text(encoding="utf-8"))
    assert saved["name"] == "pr-review-flow"
    assert saved["components"] == [
        {"component_type": "skill", "component_id": ENTITY},
        {"component_type": "mcp", "component_id": "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4c5d"},
    ]
    assert saved["supported_harnesses"] == ["claude-code", "kiro"]
    assert data["from_capabilities"]["skipped_agents"] == ["acme/pr-reviewer@3.1.0"]
    assert "Assembled from 2 resource(s)" in saved["description"]


def test_agent_init_from_capabilities_stays_interactive_with_inferred_harness(monkeypatch, tmp_path, _agent_boundaries):
    monkeypatch.chdir(tmp_path)
    capability_lock.record(
        kind="skill",
        mode="context",
        source="discover-cli",
        harness="kiro",
        cwd=tmp_path,
        identifier=URN,
        component_id=ENTITY,
        native_ref="acme/security-review@1.2.0",
        version="1.2.0",
        now=datetime.now(UTC),
    )

    result = runner.invoke(
        agent.agent_app,
        ["init", "--dir", str(tmp_path / "out"), "--from-capabilities"],
        input="my-agent\n\nReview pull requests.\n\nFollow the review checklist.\n",
    )

    assert result.exit_code == 0, result.output
    saved = yaml.safe_load((tmp_path / "out" / agent.YAML_FILE).read_text(encoding="utf-8"))
    assert saved["supported_harnesses"] == ["kiro"]
    assert saved["components"] == [{"component_type": "skill", "component_id": ENTITY}]


def test_agent_init_from_capabilities_with_empty_lock_is_not_found(monkeypatch, tmp_path, _agent_boundaries):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(agent.agent_app, ["init", "--from-capabilities", "--name", "x", "--output", "json"])
    assert result.exit_code == 5
    assert "No resources were used" in result.output


def test_agent_init_rejects_bad_since(monkeypatch, tmp_path, _agent_boundaries):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        agent.agent_app, ["init", "--from-capabilities", "--since", "soon", "--name", "x", "-o", "json"]
    )
    assert result.exit_code == 7
    assert "Invalid --since" in result.output


# ── every printed command must exist ─────────────────────────────────────

SANDBOX_URN = f"urn:air:observal.example.com:sandbox:{ENTITY}"


def _assert_command_exists(line: str) -> list[str]:
    """Resolve a printed `observal ...` command in the real Typer app and parse its arguments with Click."""
    import shlex

    import click
    import typer.main

    from observal_cli.main import app

    argv = shlex.split(line)
    assert argv[0] == "observal", line
    cmd = typer.main.get_command(app)
    ctx = click.Context(cmd, info_name="observal")
    rest = [a.replace("<harness>", "kiro") for a in argv[1:]]
    while isinstance(cmd, click.Group):
        assert rest and rest[0] in cmd.commands, f"no such command in {line!r}"
        name, rest = rest[0], rest[1:]
        cmd = cmd.commands[name]
        ctx = click.Context(cmd, info_name=name, parent=ctx)
    cmd.make_context(cmd.name, rest, parent=ctx.parent)  # raises on unknown flag or wrong argument count
    return argv


@pytest.mark.parametrize(
    ("kind", "ref", "expected"),
    [
        ("agent", "acme/rev@2.0.0", "observal agent pull acme/rev --harness kiro --version 2.0.0"),
        ("mcp", "acme/github@1.4.2", "observal registry mcp install acme/github --harness kiro --version 1.4.2"),
        ("hook", "acme/fmt@1.0.0", "observal registry hook install acme/fmt --harness kiro --version 1.0.0"),
        ("sandbox", "acme/box@3.1.0", f"observal agent add sandbox {ENTITY} --version 3.1.0"),
    ],
)
def test_use_next_step_is_a_real_command_for_every_setup_kind(monkeypatch, kind, ref, expected):
    urn = f"urn:air:observal.example.com:{kind}:{ENTITY}"
    entry = _entry(identifier=urn, **{"obs:kind": kind, "obs:nativeRef": ref})
    monkeypatch.setattr(discover.client, "get", Mock(return_value=entry))

    result = _invoke("use", urn, "--harness", "kiro", "--output", "json")

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["next_step"] == expected
    assert data["activated"] is False
    _assert_command_exists(data["next_step"])


def test_use_sandbox_says_plainly_it_is_used_through_an_agent(monkeypatch):
    entry = _entry(identifier=SANDBOX_URN, **{"obs:kind": "sandbox", "obs:nativeRef": "acme/box@3.1.0"})
    monkeypatch.setattr(discover.client, "get", Mock(return_value=entry))

    data = json.loads(_invoke("use", SANDBOX_URN, "--output", "json").output)
    assert "registry sandbox show" not in data["next_step"]
    # Meaning, not wording: it must not promise an install command, and must say the Agent has to be pulled.
    assert "no install command" in data["reason"].lower()
    assert "agent pull" in data["reason"]
    assert "confirms before writing" not in data["reason"]

    text = _invoke("use", SANDBOX_URN).output
    assert f"observal agent add sandbox {ENTITY}" in text and "restart" not in text


def test_use_sandbox_without_version_does_not_pin(monkeypatch):
    entry = _entry(identifier=SANDBOX_URN, version="", **{"obs:kind": "sandbox", "obs:nativeRef": "acme/box"})
    monkeypatch.setattr(discover.client, "get", Mock(return_value=entry))

    data = json.loads(_invoke("use", SANDBOX_URN, "--output", "json").output)
    assert data["next_step"] == f"observal agent add sandbox {ENTITY}"


def test_next_step_for_sandbox_needs_an_entity_id_and_other_kinds_have_none():
    assert discover._next_step({"obs:kind": "sandbox", "obs:nativeRef": "a/b@1.0.0"}, None) is None
    assert discover._next_step({"obs:kind": "prompt", "obs:nativeRef": "a/b@1.0.0"}, None) is None
    assert discover._next_step({"obs:kind": "mcp"}, None) is None


def test_hostile_reference_stays_a_single_valid_argument_for_the_real_command():
    step = discover._next_step({"obs:kind": "mcp", "obs:nativeRef": "a/b';rm -rf %_\\@1.0.0"}, "kiro")
    argv = _assert_command_exists(step)
    assert argv[4] == "a/b';rm -rf %_\\"


def test_context_kinds_have_no_next_step_to_print(monkeypatch):
    monkeypatch.setattr(discover.client, "get", Mock(return_value=_entry()))
    monkeypatch.setattr(discover.client, "get_text", Mock(return_value="# body"))
    data = json.loads(_invoke("use", URN, "--harness", "kiro", "--output", "json").output)
    assert data["activated"] is True and "next_step" not in data


def test_use_hints_printed_by_search_and_inspect_are_real_commands():
    a2a = "urn:air:agents.acme.com:a2a:incident-triage"
    delegate_hint = discover._use_hint({"identifier": a2a, "obs:availability": "delegate"})
    assert _assert_command_exists(delegate_hint)[:3] == ["observal", "delegate", "run"]
    assert discover._use_hint({"identifier": a2a, "obs:protocol": "a2a"}) == delegate_hint
    assert _assert_command_exists(discover._use_hint({"identifier": URN}))[:3] == ["observal", "discover", "use"]


def test_sandbox_without_native_ref_still_gets_the_agent_add_step(monkeypatch):
    entry = _entry(identifier=SANDBOX_URN, **{"obs:kind": "sandbox", "obs:nativeRef": None})
    monkeypatch.setattr(discover.client, "get", Mock(return_value=entry))

    # The entry's own version still pins the release discovery showed.
    data = json.loads(_invoke("use", SANDBOX_URN, "--output", "json").output)
    assert data["next_step"] == f"observal agent add sandbox {ENTITY} --version 1.2.0"
    assert discover._next_step({"obs:kind": "sandbox"}, None, ENTITY) == f"observal agent add sandbox {ENTITY}"
    assert discover._next_step({"obs:kind": "sandbox", "version": "1.2.0"}, None, ENTITY) == data["next_step"]

    # No step, no reference to one.
    assert discover._next_step({"obs:kind": "sandbox"}, None) is None
    monkeypatch.setattr(discover, "_next_step", lambda *a, **k: None)
    data = json.loads(_invoke("use", SANDBOX_URN, "--output", "json").output)
    assert data["next_step"] is None and "next_step" not in data["reason"]


def test_printed_sandbox_step_really_adds_the_sandbox_to_the_agent_yaml(monkeypatch, tmp_path):
    import shlex

    entry = _entry(identifier=SANDBOX_URN, **{"obs:kind": "sandbox", "obs:nativeRef": "acme/box@3.1.0"})
    monkeypatch.setattr(discover.client, "get", Mock(return_value=entry))
    step = json.loads(_invoke("use", SANDBOX_URN, "--output", "json").output)["next_step"]
    argv = shlex.split(step)
    assert argv[:3] == ["observal", "agent", "add"]

    (tmp_path / "observal-agent.yaml").write_text(yaml.safe_dump({"name": "a", "components": []}))
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(agent.agent_app, ["add", *argv[3:], "--output", "json"])

    assert result.exit_code == 0, result.output
    saved = yaml.safe_load((tmp_path / "observal-agent.yaml").read_text())
    assert saved["components"] == [{"component_type": "sandbox", "component_id": ENTITY, "version": "3.1.0"}]


def test_inspect_does_not_call_undeclared_harnesses_unrestricted(monkeypatch):
    monkeypatch.setattr(discover.client, "get", Mock(return_value=_entry(**{"obs:supportedHarnesses": []})))
    result = _invoke("inspect", URN)
    assert result.exit_code == 0, result.output
    assert "not declared" in result.output
    assert "any" not in " ".join(line for line in result.output.split("\n") if "Harnesses" in line).split()
