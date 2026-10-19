# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for _resolve_agent version attribution from lockfile."""

import json
from pathlib import Path
from unittest.mock import patch

from observal_cli.lockfile import get_agent_by_name
from observal_cli.sessions.base import _lookup_lockfile_agent, _resolve_agent


def _make_lockfile_entry(
    name: str = "my-agent",
    agent_id: str = "uuid-123",
    version: str = "1.2.0",
    directory: str = "/projects/app",
    scope: str = "project",
) -> dict:
    """Build a lockfile agent entry matching the real format from upsert_agent."""
    return {
        "name": name,
        "id": agent_id,
        "version": version,
        "pulled_at": "2026-06-17T00:00:00+00:00",
        "scope": scope,
        "directory": directory,
        "components": [
            {"type": "skill", "name": "pr-review", "id": "comp-1", "version": "1.0.0"},
        ],
    }


def _write_kiro_session(tmp_path: Path, agent_name: str) -> Path:
    session_dir = tmp_path / ".kiro" / "sessions" / "cli"
    session_dir.mkdir(parents=True)
    transcript = session_dir / "session.jsonl"
    transcript.write_text('{"kind":"Prompt"}\n')
    transcript.with_suffix(".json").write_text(
        json.dumps(
            {
                "session_state": {
                    "agent_name": agent_name,
                    "conversation_metadata": {
                        "user_turn_metadatas": [
                            {"loop_id": {"agent_id": {"name": agent_name}}},
                        ]
                    },
                }
            }
        )
    )
    return transcript


class TestResolveAgentVersionFromLockfile:
    """Verify that _resolve_agent enriches agent name with version from lockfile."""

    def test_kiro_session_agent_gets_version_from_lockfile(self, tmp_path):
        """Kiro attribution comes from session metadata, not the hook environment."""
        transcript = _write_kiro_session(tmp_path, "my-agent")
        entry = _make_lockfile_entry(name="my-agent", agent_id="uuid-123", version="1.2.0")

        with (
            patch.dict("os.environ", {"OBSERVAL_AGENT_ID": "wrong-hook-uuid"}, clear=True),
            patch("observal_cli.lockfile.get_agent_by_name", return_value=entry) as lookup,
            patch("observal_cli.sessions.base._lookup_lockfile_agent_by_id") as id_lookup,
        ):
            agent_id, agent_version = _resolve_agent("", [], transcript, harness="kiro")

        assert agent_id == "uuid-123"
        assert agent_version == "1.2.0"
        lookup.assert_called_once_with("my-agent", harness="kiro", directory=None)
        id_lookup.assert_not_called()

    def test_kiro_session_agent_missing_lockfile_returns_unattributed(self, tmp_path):
        """Unknown Kiro agent names must not fall back to a hook UUID or cwd guess."""
        transcript = _write_kiro_session(tmp_path, "local-agent")
        with (
            patch.dict("os.environ", {"OBSERVAL_AGENT_ID": "pulled-agent-uuid"}, clear=True),
            patch("observal_cli.lockfile.get_agent_by_name", return_value=None),
            patch("observal_cli.sessions.base._lookup_lockfile_agent_by_id") as id_lookup,
        ):
            agent_id, agent_version = _resolve_agent(str(tmp_path), [], transcript, harness="kiro")

        assert agent_id is None
        assert agent_version is None
        id_lookup.assert_not_called()

    def test_kiro_default_ignores_pulled_agent_hook_id(self, tmp_path):
        """Globally fired pulled-agent hooks must not claim kiro_default sessions."""
        transcript = _write_kiro_session(tmp_path, "kiro_default")
        with (
            patch.dict("os.environ", {"OBSERVAL_AGENT_ID": "pulled-agent-uuid"}, clear=True),
            patch("observal_cli.lockfile.get_agent_by_name") as name_lookup,
            patch("observal_cli.sessions.base._lookup_lockfile_agent_by_id") as id_lookup,
        ):
            agent_id, agent_version = _resolve_agent(str(tmp_path), [], transcript, harness="kiro")

        assert agent_id is None
        assert agent_version is None
        name_lookup.assert_not_called()
        id_lookup.assert_not_called()

    def test_kiro_missing_metadata_does_not_trust_hook_id(self, tmp_path):
        """Missing session metadata fails safely instead of guessing from the hook."""
        transcript = tmp_path / "missing.jsonl"
        transcript.write_text('{"kind":"Prompt"}\n')
        with (
            patch.dict("os.environ", {"OBSERVAL_AGENT_ID": "uuid-123"}, clear=True),
            patch("observal_cli.lockfile.get_agent_by_name") as name_lookup,
            patch("observal_cli.sessions.base._lookup_lockfile_agent_by_id") as id_lookup,
        ):
            agent_id, agent_version = _resolve_agent(str(tmp_path), [], transcript, harness="kiro")

        assert agent_id is None
        assert agent_version is None
        name_lookup.assert_not_called()
        id_lookup.assert_not_called()

    def test_adapter_without_identity_extension_uses_shared_resolution(self):
        """Existing structural adapters can omit the optional identity resolver."""

        class LegacyAdapter:
            @staticmethod
            def requires_explicit_agent_id() -> bool:
                return False

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("observal_cli.harness.ensure_loaded"),
            patch("observal_cli.harness.get_adapter", return_value=LegacyAdapter()),
            patch("observal_cli.sessions.base._lookup_lockfile_agent", return_value=None),
        ):
            agent_id, agent_version = _resolve_agent("", [], None, harness="legacy")

        assert agent_id is None
        assert agent_version is None

    def test_conflicting_live_markers_fail_closed(self, tmp_path):
        id_entry = _make_lockfile_entry(name="agent-a", agent_id="uuid-a")
        name_entry = _make_lockfile_entry(name="agent-b", agent_id="uuid-b")
        for id_match in (id_entry, None):
            with (
                patch.dict("os.environ", {"OBSERVAL_AGENT_ID": "uuid-a", "OBSERVAL_AGENT_NAME": "agent-b"}, clear=True),
                patch("observal_cli.sessions.base._lookup_lockfile_agent_by_id", return_value=id_match),
                patch("observal_cli.sessions.base._lookup_lockfile_agent", return_value=name_entry),
            ):
                assert _resolve_agent(str(tmp_path), [], None) == (None, None)

    def test_recovery_ignores_both_markers_and_directory(self, tmp_path):
        with (
            patch.dict("os.environ", {"OBSERVAL_AGENT_ID": "uuid-a", "OBSERVAL_AGENT_NAME": "agent-a"}, clear=True),
            patch("observal_cli.sessions.base._lookup_lockfile_agent_by_id") as by_id,
            patch("observal_cli.sessions.base._lookup_lockfile_agent") as by_name,
        ):
            assert _resolve_agent(str(tmp_path), [], None, identity_scope="recovery") == (None, None)
            by_id.assert_not_called()
            by_name.assert_not_called()

    def test_env_var_gets_version_from_lockfile(self, tmp_path):
        """When OBSERVAL_AGENT_NAME is set, version should come from lockfile."""
        entry = _make_lockfile_entry(name="my-agent", agent_id="uuid-123", version="1.2.0", directory=str(tmp_path))

        with (
            patch.dict("os.environ", {"OBSERVAL_AGENT_NAME": "my-agent"}),
            patch("observal_cli.sessions.base._lookup_lockfile_agent", return_value=entry),
        ):
            agent_id, agent_version = _resolve_agent(str(tmp_path), [], None)

        assert agent_id == "uuid-123"
        assert agent_version == "1.2.0"

    def test_env_var_without_lockfile_returns_none_version(self):
        """When lockfile is missing, version should be None (graceful degradation)."""
        with (
            patch.dict("os.environ", {"OBSERVAL_AGENT_NAME": "my-agent"}),
            patch("observal_cli.sessions.base._lookup_lockfile_agent", return_value=None),
        ):
            agent_id, agent_version = _resolve_agent("/some/dir", [], None)

        assert agent_id == "my-agent"
        assert agent_version is None

    def test_jsonl_agent_setting_gets_version_from_lockfile(self, tmp_path):
        """When agent is resolved from JSONL, version should come from lockfile."""
        lines = [json.dumps({"type": "agent-setting", "agentSetting": "cc-agent"})]
        entry = _make_lockfile_entry(name="cc-agent", agent_id="uuid-456", version="2.0.1", directory=str(tmp_path))

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("observal_cli.sessions.base._lookup_lockfile_agent", return_value=entry),
        ):
            import os

            os.environ.pop("OBSERVAL_AGENT_NAME", None)
            agent_id, agent_version = _resolve_agent(str(tmp_path), lines, None)

        assert agent_id == "uuid-456"
        assert agent_version == "2.0.1"

    def test_lockfile_fallback_when_no_env_or_jsonl(self, tmp_path):
        """A harness that cannot name its agent falls back to the agent pulled into the directory."""
        entry = _make_lockfile_entry(
            name="fallback-agent", agent_id="uuid-789", version="3.0.0", directory=str(tmp_path)
        )

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("observal_cli.sessions.base._lookup_lockfile_agent", return_value=entry),
        ):
            import os

            os.environ.pop("OBSERVAL_AGENT_NAME", None)
            agent_id, agent_version = _resolve_agent(str(tmp_path), [], None, harness="cursor")

        assert agent_id == "uuid-789"
        assert agent_version == "3.0.0"

    def test_no_sources_returns_none(self):
        """When nothing matches, returns (None, None)."""
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("observal_cli.sessions.base._lookup_lockfile_agent", return_value=None),
        ):
            import os

            os.environ.pop("OBSERVAL_AGENT_NAME", None)
            agent_id, agent_version = _resolve_agent("/empty/dir", [], None)

        assert agent_id is None
        assert agent_version is None

    def test_env_var_name_mismatch_returns_none_version(self, tmp_path):
        """When env agent name differs from lockfile name, version is None.

        We only attribute a version when the lockfile confirms the agent.
        """
        entry = _make_lockfile_entry(
            name="other-agent", agent_id="uuid-other", version="1.0.0", directory=str(tmp_path)
        )

        with (
            patch.dict("os.environ", {"OBSERVAL_AGENT_NAME": "my-agent"}),
            patch("observal_cli.sessions.base._lookup_lockfile_agent", return_value=entry),
        ):
            agent_id, agent_version = _resolve_agent(str(tmp_path), [], None)

        # Name comes from env var (not lockfile) since they don't match
        assert agent_id == "my-agent"
        # Version is None because lockfile entry doesn't match this agent
        assert agent_version is None

    def test_empty_cwd_uses_agent_name_lockfile_lookup(self):
        """When cwd is empty, per-agent hooks can still resolve the version by name."""
        entry = _make_lockfile_entry(name="my-agent", agent_id="uuid-123", version="1.2.0")

        with (
            patch.dict("os.environ", {"OBSERVAL_AGENT_NAME": "my-agent"}),
            patch("observal_cli.sessions.base._lookup_lockfile_agent", return_value=entry),
        ):
            agent_id, agent_version = _resolve_agent("", [], None)

        assert agent_id == "uuid-123"
        assert agent_version == "1.2.0"

    def test_lockfile_lookup_prefers_named_agent_over_first_directory_match(self, tmp_path):
        """Multiple agents can share a directory, so name must disambiguate."""
        data = {
            "harnesses": {
                "kiro": {
                    "agents": [
                        _make_lockfile_entry(
                            name="old-agent", agent_id="old-uuid", version="1.0.0", directory=str(tmp_path)
                        ),
                        _make_lockfile_entry(
                            name="my-agent", agent_id="new-uuid", version="1.2.0", directory=str(tmp_path)
                        ),
                    ]
                }
            }
        }

        with (
            patch("observal_shared.harness_registry.get_valid_harnesses", return_value=["kiro"]),
            patch("observal_cli.lockfile.read_registry_lockfile", return_value=({}, data)),
        ):
            entry = _lookup_lockfile_agent(str(tmp_path), agent_name="my-agent")

        assert entry is not None
        assert entry["id"] == "new-uuid"
        assert entry["version"] == "1.2.0"

    def test_lockfile_lookup_uses_agent_name_when_cwd_does_not_match(self, tmp_path):
        """Kiro hook cwd can differ from the user-scoped lockfile directory."""
        data = {
            "harnesses": {
                "kiro": {
                    "agents": [
                        _make_lockfile_entry(
                            name="my-agent",
                            agent_id="uuid-123",
                            version="1.2.0",
                            directory=str(tmp_path / ".observal"),
                            scope="user",
                        )
                    ]
                }
            }
        }

        with (
            patch("observal_shared.harness_registry.get_valid_harnesses", return_value=["kiro"]),
            patch("observal_cli.lockfile.read_registry_lockfile", return_value=({}, data)),
        ):
            entry = _lookup_lockfile_agent(str(tmp_path / "repo"), agent_name="my-agent")

        assert entry is not None
        assert entry["version"] == "1.2.0"

    def test_kiro_lockfile_lookup_matches_generated_local_name(self):
        """Session metadata records the generated Kiro profile name, not its display name."""
        entry = _make_lockfile_entry(name="Display Name", agent_id="uuid-local", version="2.0.0", scope="user")
        entry["local_name"] = "alice-shared"
        data = {"harnesses": {"kiro": {"agents": [entry]}}}

        with patch("observal_cli.lockfile.read_registry_lockfile", return_value=({}, data)):
            matched = get_agent_by_name("alice-shared", harness="kiro")

        assert matched is entry

    def test_kiro_lockfile_lookup_fails_closed_for_ambiguous_projects(self):
        """Recovery without cwd must not choose an arbitrary project-scoped agent."""
        first = _make_lockfile_entry(name="Agent", agent_id="uuid-one", directory="/project/one")
        second = _make_lockfile_entry(name="Agent", agent_id="uuid-two", directory="/project/two")
        first["local_name"] = second["local_name"] = "shared-agent"
        data = {"harnesses": {"kiro": {"agents": [first, second]}}}

        with patch("observal_cli.lockfile.read_registry_lockfile", return_value=({}, data)):
            ambiguous = get_agent_by_name("shared-agent", harness="kiro")
            scoped = get_agent_by_name("shared-agent", harness="kiro", directory="/project/two")

        assert ambiguous is None
        assert scoped is second
