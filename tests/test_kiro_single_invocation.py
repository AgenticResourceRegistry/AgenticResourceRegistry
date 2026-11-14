# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Configuration, not server deduplication, must select one V3 telemetry invocation."""

import json
from pathlib import Path

import pytest

from observal_cli import cmd_doctor as doctor
from observal_cli import lockfile
from observal_cli.harness.kiro import KiroAdapter
from observal_cli.harness_specs.kiro_hooks_spec import build_kiro_hooks, build_kiro_hooks_file, iter_kiro_hooks


@pytest.fixture
def scopes(tmp_path, monkeypatch):
    """Isolate runtime/config roots and avoid invoking the native CLI in unit tests."""
    home = tmp_path / "home"
    root = home / "relocated-kiro"
    project = tmp_path / "project"
    root.mkdir(parents=True)
    project.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("KIRO_HOME", str(root))
    monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "2.35.0")
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", "v3")
    monkeypatch.setenv("OBSERVAL_KIRO_IDE", "0")
    monkeypatch.chdir(project)
    return root, project


def standalone(root, triggers=("UserPromptSubmit", "Stop")):
    """Write a structurally valid standalone configuration for chosen lifecycle events."""
    path = root / "hooks/observal.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = build_kiro_hooks_file()
    data["hooks"] = [hook for hook in data["hooks"] if hook["trigger"] in triggers]
    path.write_text(json.dumps(data))
    return path


def profile(root, universal):
    """Create owned and unrelated inline hooks while preserving profile format."""
    path = root / "agents/probe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    owned = build_kiro_hooks(agent_id="agent-id")
    if universal:
        hooks = [
            {
                "name": "observal-session-push-" + h.trigger.lower(),
                "trigger": h.trigger,
                "action": {"type": "command", **h.handler},
            }
            for h in iter_kiro_hooks(owned)
        ]
        hooks.append(
            {
                "name": "user-hook",
                "trigger": "Stop",
                "action": {"type": "command", "command": "echo user"},
                "timeout": 17,
            }
        )
    else:
        hooks = {**owned, "custom": [{"command": "echo user"}], "future": {"keep": True}}
    data = {"name": "probe", "hooks": hooks, "permissions": {"rules": []}, "kiroUpgrade": {"keep": True}}
    path.write_text(json.dumps(data))
    return path, data


def inline_events(data):
    """Count Observal invocation routes without relying on transport-side deduplication."""
    return [
        hook.trigger.lower()
        for hook in iter_kiro_hooks(data.get("hooks"))
        if "observal_cli.hooks.session_push" in hook.command
    ]


@pytest.mark.parametrize("universal", [False, True])
@pytest.mark.parametrize("scope", ["user", "project"])
def test_patch_removes_covered_v3_inline_invocations(scopes, monkeypatch, universal, scope):
    """Repair an upgraded profile so V3 cannot invoke both inline and standalone copies."""
    root, project = scopes
    target = root if scope == "user" else project / ".kiro"
    path, original = profile(target, universal)
    standalone(root)
    monkeypatch.setattr(
        lockfile,
        "read_registry_lockfile",
        lambda: (
            {},
            {
                "harnesses": {
                    "kiro": {
                        "agents": [{"id": "agent-id", "local_name": "probe", "scope": scope, "directory": str(project)}]
                    }
                }
            },
        ),
    )
    assert doctor._patch_kiro(False)
    updated = json.loads(path.read_text())
    assert inline_events(updated) == []
    assert updated["permissions"] == original["permissions"] and updated["kiroUpgrade"] == original["kiroUpgrade"]
    if universal:
        assert updated["hooks"] == [original["hooks"][-1]]
    else:
        assert updated["hooks"] == {"custom": [{"command": "echo user"}], "future": {"keep": True}}
    assert not doctor._patch_kiro(False)


@pytest.mark.parametrize("universal", [False, True])
def test_v2_keeps_inline_routes(scopes, monkeypatch, universal):
    """V2 retains its hooks even when a standalone file exists for V3."""
    root, project = scopes
    standalone(root)
    _, data = profile(project / ".kiro", universal)
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", "v2")
    assert set(inline_events(KiroAdapter().rewrite_agent_profile(data, "agent-id"))) == {"userpromptsubmit", "stop"}


@pytest.mark.parametrize("global_engine", ["v2", "v3"])
@pytest.mark.parametrize("universal", [False, True])
@pytest.mark.parametrize("operation", ["rewrite", "patch", "dry-run"])
def test_global_engine_wins_when_workspaces_conflict_and_cli_is_missing(
    scopes, monkeypatch, global_engine, universal, operation
):
    """Native global settings win during rewrite, patch, and preview even without a CLI on PATH."""
    root, caller = scopes
    global_settings = root / "settings/cli.json"
    global_settings.parent.mkdir()
    global_settings.write_text(json.dumps({"chat.agentEngine": global_engine}))
    target = caller.parent / "target"
    other = "v3" if global_engine == "v2" else "v2"
    for workspace, engine in [(caller, other), (target, other)]:
        settings = workspace / ".kiro/settings/cli.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"chat.agentEngine": engine}))
    standalone(root)
    path, original = profile(target / ".kiro", universal)
    monkeypatch.delenv("OBSERVAL_KIRO_ENGINE", raising=False)
    monkeypatch.delenv("OBSERVAL_KIRO_CLI_VERSION", raising=False)
    monkeypatch.setattr("observal_cli.harness.kiro.shutil.which", lambda _name: None)
    if operation == "rewrite":
        updated = KiroAdapter().rewrite_agent_profile(original, "agent-id", path=path)
    else:
        monkeypatch.setattr(
            lockfile,
            "read_registry_lockfile",
            lambda: (
                {},
                {
                    "harnesses": {
                        "kiro": {
                            "agents": [
                                {"id": "agent-id", "local_name": "probe", "scope": "project", "directory": str(target)}
                            ]
                        }
                    }
                },
            ),
        )
        rewrites = []
        rewrite = KiroAdapter.rewrite_agent_profile

        def record_rewrite(self, *args, **kwargs):
            """Observe the proposed profile as well as the applied one for doctor dry runs."""
            result = rewrite(self, *args, **kwargs)
            rewrites.append(result)
            return result

        monkeypatch.setattr(KiroAdapter, "rewrite_agent_profile", record_rewrite)
        before = path.read_bytes()
        assert doctor._patch_kiro(operation == "dry-run")
        updated = rewrites[0]
        if operation == "dry-run":
            assert path.read_bytes() == before
        else:
            assert json.loads(path.read_text()) == updated
    assert set(inline_events(updated)) == ({"userpromptsubmit", "stop"} if global_engine == "v2" else set())
    assert updated["permissions"] == original["permissions"]
    assert updated["kiroUpgrade"] == original["kiroUpgrade"]


@pytest.mark.parametrize("saved,override", [("v2", "v3"), ("v3", "v2")])
@pytest.mark.parametrize("universal", [False, True])
def test_prepatched_override_has_one_route_and_restores_every_locked_profile(
    scopes, monkeypatch, saved, override, universal
):
    """A temporary override is inventory-wide; restore saved placements before other chats launch."""
    root, caller = scopes
    standalone(root)
    settings = root / "settings/cli.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"chat.agentEngine": saved}))
    targets = {}
    agents = []
    for workspace_setting in ("v2", "v3"):
        project = caller.parent / ("workspace-" + workspace_setting)
        local = project / ".kiro/settings/cli.json"
        local.parent.mkdir(parents=True)
        local.write_text(json.dumps({"chat.agentEngine": workspace_setting}))
        path, original = profile(project / ".kiro", universal)
        targets[workspace_setting] = (project, path, original)
        agents.append({"id": "agent-id", "local_name": "probe", "scope": "project", "directory": str(project)})
    monkeypatch.setattr(lockfile, "read_registry_lockfile", lambda: ({}, {"harnesses": {"kiro": {"agents": agents}}}))
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", override)
    assert doctor._patch_kiro(False)
    for project, path, original in targets.values():
        updated = json.loads(path.read_text())
        inline = inline_events(updated)
        assert len(inline) == (2 if override == "v2" else 0)
        global_events = {e.lower() for e in KiroAdapter._v1_hook_events(root)} if override == "v3" else set()
        local_events = (
            {e.lower() for e in KiroAdapter._v1_hook_events(project / ".kiro")} if override == "v3" else set()
        )
        for event in ("userpromptsubmit", "stop"):
            assert inline.count(event) + int(event in global_events) + int(event in local_events) == 1
        assert updated["permissions"] == original["permissions"]
        assert updated["kiroUpgrade"] == original["kiroUpgrade"]
    monkeypatch.delenv("OBSERVAL_KIRO_ENGINE")
    assert doctor._patch_kiro(False)
    for _project, path, original in targets.values():
        restored = json.loads(path.read_text())
        assert len(inline_events(restored)) == (2 if saved == "v2" else 0)
        assert restored["permissions"] == original["permissions"]
        assert restored["kiroUpgrade"] == original["kiroUpgrade"]


@pytest.mark.parametrize("triggers", [(), ("UserPromptSubmit",), ("Stop",)])
def test_missing_or_partial_coverage_keeps_only_needed_inline_routes(scopes, triggers):
    """An inline fallback is removed only for its own valid standalone event."""
    root, project = scopes
    standalone(root, triggers)
    _, data = profile(project / ".kiro", True)
    updated = KiroAdapter().rewrite_agent_profile(data, "agent-id")
    expected = {"userpromptsubmit", "stop"} - {trigger.lower() for trigger in triggers}
    assert set(inline_events(updated)) == expected


def test_patch_deduplicates_global_and_project_standalone_hooks(scopes, monkeypatch):
    """Global coverage must also prevent a second workspace standalone invocation."""
    root, project = scopes
    path, _ = profile(project / ".kiro", True)
    standalone(root)
    local = standalone(project / ".kiro")
    local_data = json.loads(local.read_text())
    foreign = {"name": "user", "trigger": "Stop", "action": {"type": "command", "command": "echo user"}}
    local_data["hooks"].append(foreign)
    local.write_text(json.dumps(local_data))
    monkeypatch.setattr(
        lockfile,
        "read_registry_lockfile",
        lambda: (
            {},
            {
                "harnesses": {
                    "kiro": {
                        "agents": [
                            {"id": "agent-id", "local_name": "probe", "scope": "project", "directory": str(project)}
                        ]
                    }
                }
            },
        ),
    )
    assert doctor._patch_kiro(False)
    assert json.loads(local.read_text())["hooks"] == [foreign]
    assert inline_events(json.loads(path.read_text())) == []


def test_dry_run_does_not_remove_inline_or_standalone_bytes(scopes, monkeypatch):
    """Preview both placement repairs without editing configuration."""
    root, project = scopes
    path, _ = profile(project / ".kiro", True)
    hooks = standalone(root)
    monkeypatch.setattr(
        lockfile,
        "read_registry_lockfile",
        lambda: (
            {},
            {
                "harnesses": {
                    "kiro": {
                        "agents": [
                            {"id": "agent-id", "local_name": "probe", "scope": "project", "directory": str(project)}
                        ]
                    }
                }
            },
        ),
    )
    before = path.read_bytes(), hooks.read_bytes()
    assert doctor._patch_kiro(True)
    assert (path.read_bytes(), hooks.read_bytes()) == before


@pytest.mark.parametrize("user_scope", [False, True])
def test_real_install_path_selects_one_v3_route(scopes, user_scope):
    """Pull localization uses the target scope and writes replacement coverage first."""
    from observal_cli.cmd_pull import write_install_snippet

    root, project = scopes
    standalone(root)
    target = root if user_scope else project / ".kiro"
    original_path, data = profile(target, True)
    hooks_path = target / "hooks/observal.json"
    snippet = {
        "hooks_config": {"path": str(hooks_path), "content": build_kiro_hooks_file()},
        "agent_profile": {"path": str(original_path), "content": data},
    }
    written, failures = write_install_snippet(
        snippet,
        harness="kiro",
        adapter=KiroAdapter(),
        target_dir=root if user_scope else project,
        agent_id="agent-id",
        is_user_scope=user_scope,
    )
    assert failures == [] and len(written) == 2
    assert inline_events(json.loads(original_path.read_text())) == []
    assert KiroAdapter._v1_hook_events(root) == {"Stop", "UserPromptSubmit"}
    if not user_scope:
        assert KiroAdapter._v1_hook_events(target) == set()


@pytest.mark.parametrize("global_engine", ["v2", "v3"])
def test_workspace_entries_cannot_change_global_hook_placement(scopes, monkeypatch, global_engine):
    """A project cli.json entry cannot select the native harness or its hook placement."""
    root, project = scopes
    standalone(root)
    monkeypatch.delenv("OBSERVAL_KIRO_ENGINE")
    global_settings = root / "settings/cli.json"
    global_settings.parent.mkdir()
    global_settings.write_text(json.dumps({"chat.agentEngine": global_engine}))
    target = project / "other-project"
    settings = target / ".kiro/settings/cli.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"chat.agentEngine": "v3" if global_engine == "v2" else "v2"}))
    path, data = profile(target / ".kiro", True)
    updated = KiroAdapter().rewrite_agent_profile(data, "agent-id", path=path)
    assert set(inline_events(updated)) == ({"stop", "userpromptsubmit"} if global_engine == "v2" else set())


def test_failed_replacement_write_leaves_inline_fallback(scopes, monkeypatch):
    """A permission failure cannot remove hooks before installing their replacement."""
    root, project = scopes
    path, data = profile(project / ".kiro", True)
    before = path.read_bytes()
    monkeypatch.setattr(
        lockfile,
        "read_registry_lockfile",
        lambda: (
            {},
            {
                "harnesses": {
                    "kiro": {
                        "agents": [
                            {"id": "agent-id", "local_name": "probe", "scope": "project", "directory": str(project)}
                        ]
                    }
                }
            },
        ),
    )

    def denied_write(*args):
        """Model a failed standalone hook installation."""
        raise PermissionError("standalone directory is read-only")

    monkeypatch.setattr(doctor, "atomic_write", denied_write)
    with pytest.raises(PermissionError):
        doctor._patch_kiro(False)
    assert path.read_bytes() == before


def test_user_package_wrapper_is_not_owned_telemetry(scopes):
    """The package-name substring alone must not delete a user's hook."""
    root, project = scopes
    standalone(root)
    _, data = profile(project / ".kiro", False)
    wrapper = {"command": "python /user/observal_cli_wrapper"}
    data["hooks"]["stop"].append(wrapper)
    updated = KiroAdapter().rewrite_agent_profile(data, "agent-id")
    assert updated["hooks"]["stop"] == [wrapper]


@pytest.fixture(params=["kiro-home", "home"])
def aliased_scope(scopes, monkeypatch, request):
    """Use a genuine symlink while passing canonical install paths, as pull does."""
    root, project = scopes
    alias = root.parent / "alias"
    target = root if request.param == "kiro-home" else root.parent
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks unavailable on this platform")
    if request.param == "kiro-home":
        monkeypatch.setenv("KIRO_HOME", str(alias))
    else:
        monkeypatch.delenv("KIRO_HOME")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: alias))
        root = target / ".kiro"
        root.mkdir()
    standalone(root)
    return root, project


def test_user_standalone_rewrite_preserves_hooks_under_alias(aliased_scope):
    """A canonical path and its symlink alias identify the same user-scope hook file."""
    root, _ = aliased_scope
    updated = KiroAdapter().rewrite_hooks(build_kiro_hooks_file(), "agent-id", path=root / "hooks/observal.json")
    assert KiroAdapter._v1_hook_events_in(updated) == {"Stop", "UserPromptSubmit"}


def test_user_profile_under_alias_keeps_global_engine(aliased_scope, monkeypatch):
    """Canonical and aliased user roots still use global V3 over a conflicting workspace V2 entry."""
    root, project = aliased_scope
    monkeypatch.delenv("OBSERVAL_KIRO_ENGINE")
    global_settings = root / "settings/cli.json"
    global_settings.parent.mkdir()
    global_settings.write_text('{"chat.agentEngine":"v3"}')
    local_settings = project / ".kiro/settings/cli.json"
    local_settings.parent.mkdir(parents=True)
    local_settings.write_text('{"chat.agentEngine":"v2"}')
    path, content = profile(root, True)
    updated = KiroAdapter().rewrite_agent_profile(content, "agent-id", path=path)
    assert inline_events(updated) == []


def test_relocated_runtime_does_not_strip_default_home_routes(scopes):
    """Coverage in another runtime cannot suppress the default user-scope hook file."""
    root, _ = scopes
    standalone(root)
    default_root = Path.home() / ".kiro"
    standalone(default_root)
    adapter = KiroAdapter()
    rewritten = adapter.rewrite_hooks(build_kiro_hooks_file(), "agent-id", path=default_root / "hooks/observal.json")
    assert adapter._v1_hook_events_in(rewritten) == {"Stop", "UserPromptSubmit"}
    path, content = profile(default_root, True)
    assert set(inline_events(adapter.rewrite_agent_profile(content, "agent-id", path=path))) == {
        "stop",
        "userpromptsubmit",
    }


def test_default_home_return_keeps_delivery_after_user_pull(scopes, monkeypatch):
    """A default-home destination remains usable after leaving a different runtime root."""
    from observal_cli.cmd_pull import write_install_snippet

    root, project = scopes
    standalone(root)
    default_root = Path.home() / ".kiro"
    path, content = profile(default_root, True)
    snippet = {
        "hooks_config": {"path": "~/.kiro/hooks/observal.json", "content": build_kiro_hooks_file()},
        "agent_profile": {"path": "~/.kiro/agents/probe.json", "content": content},
    }
    write_install_snippet(
        snippet, harness="kiro", adapter=KiroAdapter(), target_dir=Path.home(), agent_id="agent-id", is_user_scope=True
    )
    monkeypatch.delenv("KIRO_HOME")
    adapter = KiroAdapter()
    assert adapter._v1_hook_events(default_root) == {"Stop", "UserPromptSubmit"}
    assert inline_events(adapter.rewrite_agent_profile(json.loads(path.read_text()), "agent-id", path=path)) == []
