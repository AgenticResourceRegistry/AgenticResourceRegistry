# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""observal_cli launchers never import from the project, and still start everywhere.

Hooks run in the project directory, and ``python -m`` puts the working directory
first on sys.path, so a repository containing ``observal_cli/`` could replace
Observal's code; an inherited ``PYTHONPATH`` pointing at a project could too.
An installed interpreter launches with ``-I``; a source checkout with ``-P`` and
an explicit ``PYTHONPATH`` set to the package root.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from observal_cli import cmd_pull
from observal_cli.harness_specs import (
    antigravity_hooks_spec,
    claude_code_hooks_spec,
    codex_hooks_spec,
    copilot_cli_hooks_spec,
    copilot_hooks_spec,
    goose_hooks_spec,
    kiro_hooks_spec,
)
from observal_cli.shared import launcher

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX launchers")
_LAUNCHER = re.compile(r"^(?P<prefix>.*?) (?P<flag>-[IP]) -m observal_cli\.[A-Za-z_.]+")
_PROBE = "observal_cli.hook_gate"  # imports observal_cli, prints usage to stderr, exits 1


def _clean_env(home: Path) -> dict[str, str]:
    """The environment a harness gives a hook: nothing inherited from this test run."""
    return {"PATH": "/usr/bin:/bin", "HOME": str(home)}


def _generated_commands() -> dict[str, str]:
    outputs = {
        "claude-code": claude_code_hooks_spec.get_desired_hooks(),
        "codex": codex_hooks_spec.build_codex_hooks(),
        "kiro": kiro_hooks_spec.build_kiro_hooks(),
        "copilot-cli": copilot_cli_hooks_spec.build_copilot_cli_hooks(),
        "antigravity": antigravity_hooks_spec.build_antigravity_hooks(),
        "copilot": copilot_hooks_spec.build_copilot_hooks(),
        "goose": goose_hooks_spec.hook_command(),
        "cursor (doctor)": launcher.posix_module_command("observal_cli.hooks.session_push"),
    }
    commands = {}
    for name, output in outputs.items():
        if isinstance(output, str):
            values = [output]
        else:
            values = [json.loads(f'"{raw}"') for raw in re.findall(r'"((?:[^"\\]|\\.)*)"', json.dumps(output))]
        values = [value for value in values if " -m observal_cli." in value]
        assert values, name
        commands[name] = values[0]
    return commands


def _probe(command: str) -> str:
    match = _LAUNCHER.match(command)
    assert match, command
    return f"{match['prefix']} {match['flag']} -m {_PROBE}"


def _hostile_project(root: Path) -> Path:
    """A project that would replace observal_cli if it were ever on sys.path."""
    fake = root / "observal_cli"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text("")
    for module in ("hook_gate.py", "__main__.py"):
        (fake / module).write_text("import sys; sys.stdout.write('HIJACKED'); sys.exit(0)")
    return root


def _run_hostile(command: str, project: Path) -> subprocess.CompletedProcess:
    """Run a launcher from the hostile project, with PYTHONPATH pointing at it."""
    env = {**_clean_env(project), "PYTHONPATH": str(project)}
    return subprocess.run(["/bin/sh", "-c", command], cwd=project, env=env, capture_output=True, check=False)


def _genuine(result: subprocess.CompletedProcess) -> bool:
    return b"HIJACKED" not in result.stdout and b"usage" in result.stderr


def _installed_python(tmp_path: Path) -> str:
    """A real interpreter importing observal_cli from its own site-packages, like an installed CLI."""
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    python = str(venv / "bin" / "python")
    site = subprocess.run(
        [python, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    (Path(site) / "observal_cli_source.pth").write_text(launcher.package_root() + "\n")
    return python


def test_every_generated_launcher_starts_from_another_directory_without_pythonpath(tmp_path):
    """Regression: an in-process importability check made source checkouts fail outside the repo."""
    for name, command in _generated_commands().items():
        result = subprocess.run(
            ["/bin/sh", "-c", _probe(command)], cwd=tmp_path, env=_clean_env(tmp_path), capture_output=True, check=False
        )
        assert _genuine(result), (name, result.stderr[-300:])


def test_every_generated_launcher_ignores_a_hostile_project_and_pythonpath(tmp_path):
    project = _hostile_project(tmp_path / "project")
    for name, command in _generated_commands().items():
        result = _run_hostile(_probe(command), project)
        assert _genuine(result), (name, result.stdout, result.stderr[-200:])


def test_installed_launcher_uses_isolated_mode_and_ignores_a_hostile_pythonpath(tmp_path):
    python = _installed_python(tmp_path)
    command = subprocess.run(
        [python, "-c", f"from observal_cli.shared import launcher; print(launcher.posix_module_command({_PROBE!r}))"],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert command == f"{shlex.quote(python)} -I -m {_PROBE}", command
    project = _hostile_project(tmp_path / "project")
    assert _genuine(_run_hostile(command, project))
    # The probe is genuinely hostile: -P alone honours the inherited PYTHONPATH.
    assert b"HIJACKED" in _run_hostile(f"{shlex.quote(python)} -P -m {_PROBE}", project).stdout


def test_source_checkout_launcher_replaces_a_hostile_pythonpath(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: False)
    command = launcher.posix_module_command(_PROBE)
    assert command == f"PYTHONPATH={shlex.quote(launcher.package_root())} {shlex.quote(sys.executable)} -P -m {_PROBE}"
    assert _genuine(_run_hostile(command, _hostile_project(tmp_path / "project")))


def test_flags_and_environment_follow_the_isolated_check(monkeypatch):
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: True)
    assert (launcher.isolation_flag(), launcher.pythonpath_env()) == ("-I", {})
    assert launcher.posix_prefix() == shlex.quote(sys.executable)
    assert claude_code_hooks_spec.get_desired_hooks()["Stop"][0]["hooks"][0]["command"].startswith(
        f"{sys.executable} -I -m "
    )
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: False)
    assert (launcher.isolation_flag(), launcher.pythonpath_env()) == ("-P", {"PYTHONPATH": launcher.package_root()})


def test_importability_is_checked_in_isolation_not_in_this_process():
    """This test process imports observal_cli through PYTHONPATH; an isolated interpreter may not."""
    launcher.importable_in_isolation.cache_clear()
    isolated = (
        subprocess.run(
            [sys.executable, "-I", "-c", "import observal_cli"], cwd="/", capture_output=True, check=False
        ).returncode
        == 0
    )
    assert launcher.importable_in_isolation() is isolated


PROFILE = """---
name: reviewer
description: "Mention python3 -m observal_cli.hooks.session_push here too"
hooks:
  UserPromptSubmit:
    - hooks:
        - type: command
          command: "python3 -m observal_cli.hooks.session_push"
  Stop:
    - hooks:
        - type: command
          command: python3 -m observal_cli.hooks.session_push --harness claude-code
        - type: command
          command: "make lint"
---

Tell users they can run python3 -m observal_cli.hooks.session_push by hand.
"""


@pytest.mark.parametrize(
    "interpreter", ["/opt/py/bin/python3", '/opt/py"q/bin/python3', "/opt/py 3/bin/python3", "/opt/pÿ\\x/bin/python3"]
)
def test_frontmatter_rewrite_touches_only_hook_commands_and_stays_valid_yaml(monkeypatch, interpreter):
    monkeypatch.setattr(launcher.sys, "executable", interpreter)
    monkeypatch.setattr(cmd_pull.sys, "executable", interpreter)
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: True)
    out = cmd_pull.rewrite_frontmatter_hook_launchers(PROFILE)
    head, body = out.split("\n---\n", 1)
    assert body == PROFILE.split("\n---\n", 1)[1], "the instructions are untouched"
    data = yaml.safe_load(head[4:])
    assert data["description"] == "Mention python3 -m observal_cli.hooks.session_push here too"
    expected = f"{shlex.quote(interpreter)} -I -m observal_cli.hooks.session_push"
    assert data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == expected
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == expected + " --harness claude-code"
    assert data["hooks"]["Stop"][0]["hooks"][1]["command"] == "make lint"
    command = shlex.split(data["hooks"]["Stop"][0]["hooks"][0]["command"])
    assert command[:4] == [interpreter, "-I", "-m", "observal_cli.hooks.session_push"]


def test_unparseable_or_unexpected_frontmatter_is_left_unchanged():
    broken = "---\nhooks: [unclosed\n---\nbody python3 -m observal_cli.x\n"
    assert cmd_pull.rewrite_frontmatter_hook_launchers(broken) == broken
    no_hooks = "---\nname: a\n---\npython3 -m observal_cli.x\n"
    assert cmd_pull.rewrite_frontmatter_hook_launchers(no_hooks) == no_hooks
    plain = "no frontmatter python3 -m observal_cli.x"
    assert cmd_pull.rewrite_frontmatter_hook_launchers(plain) == plain


def test_hook_json_is_rewritten_structurally_and_stays_valid(monkeypatch):
    monkeypatch.setattr(launcher.sys, "executable", '/opt/py"q/bin/python3')
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: True)
    content = {
        "hooks": {"Stop": [{"command": "OBSERVAL_AGENT_ID=x python3 -m observal_cli.hooks.session_push"}]},
        "note": "unrelated",
    }
    out = cmd_pull.rewrite_launchers_in_strings(content)
    assert json.loads(json.dumps(out)) == out
    command = shlex.split(out["hooks"]["Stop"][0]["command"])
    assert command[:5] == [
        "OBSERVAL_AGENT_ID=x",
        '/opt/py"q/bin/python3',
        "-I",
        "-m",
        "observal_cli.hooks.session_push",
    ]
    assert out["note"] == "unrelated"


def test_mcp_entries_and_argv_carry_the_trusted_fallback(monkeypatch):
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: False)
    root = launcher.package_root()
    entry = cmd_pull.rewrite_observal_interpreter(
        {"command": "python3", "args": ["-m", "observal_cli.delegation.mcp_server"], "env": {"A": "1"}}
    )
    assert entry == {
        "command": sys.executable,
        "args": ["-P", "-m", "observal_cli.delegation.mcp_server"],
        "env": {"A": "1", "PYTHONPATH": root},
    }
    argv = cmd_pull.rewrite_observal_interpreter(["claude", "mcp", "add", "x", "--", "python", "-m", "observal_cli.a"])
    assert argv == [
        "claude",
        "mcp",
        "add",
        "x",
        "--",
        "env",
        f"PYTHONPATH={root}",
        sys.executable,
        "-P",
        "-m",
        "observal_cli.a",
    ]
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: True)
    assert cmd_pull.rewrite_observal_interpreter(["python3", "-m", "observal_cli.a"]) == [
        sys.executable,
        "-I",
        "-m",
        "observal_cli.a",
    ]
    assert cmd_pull.rewrite_observal_interpreter(["/usr/bin/python3", "-m", "x"]) == ["/usr/bin/python3", "-m", "x"]


@pytest.mark.parametrize("installed", [True, False])
def test_rewritten_mcp_entry_and_argv_start_and_ignore_a_hostile_pythonpath(tmp_path, monkeypatch, installed):
    python = _installed_python(tmp_path) if installed else sys.executable
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: installed)
    monkeypatch.setattr(cmd_pull.sys, "executable", python)
    project = _hostile_project(tmp_path / "project")
    hostile = {**_clean_env(project), "PYTHONPATH": str(project)}
    entry = cmd_pull.rewrite_observal_interpreter({"command": "python3", "args": ["-m", _PROBE]})
    result = subprocess.run(
        [entry["command"], *entry["args"]],
        cwd=project,
        env={**hostile, **entry.get("env", {})},
        capture_output=True,
        check=False,
    )
    assert _genuine(result), result.stderr[-200:]
    argv = cmd_pull.rewrite_observal_interpreter(["python3", "-m", _PROBE])
    assert _genuine(subprocess.run(argv, cwd=project, env=hostile, capture_output=True, check=False))
    clean = subprocess.run(
        [entry["command"], *entry["args"]],
        cwd=tmp_path,
        env={**_clean_env(tmp_path), **entry.get("env", {})},
        capture_output=True,
        check=False,
    )
    assert _genuine(clean), "and it starts from an outside directory with no inherited PYTHONPATH"


def _copilot_cli_profile(mcp_configs: dict) -> str:
    """The agent profile the real Copilot CLI generator writes."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "observal-server"))
    from services.harness import ConfigContext
    from services.harness.copilot_cli import CopilotCliAdapter

    ctx = ConfigContext(
        agent=None,
        safe_name="reviewer",
        harness="copilot-cli",
        observal_url="http://localhost:8000",
        mcp_configs=mcp_configs,
        rules_content="Run python3 -m observal_cli.delegation.mcp_server only if asked.",
        options={"scope": "project"},
    )
    return CopilotCliAdapter().format_config(ctx)["agent_profile"]["content"]


def _frontmatter(text: str) -> dict:
    return yaml.safe_load(text.split("\n---", 1)[0][4:])


@pytest.mark.parametrize("installed", [True, False])
def test_copilot_cli_profile_delegation_mcp_launcher_is_rewritten_safely(monkeypatch, installed):
    """Regression: the generated profile's mcp-servers entry kept a bare python3 -m observal_cli launcher."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "observal-server"))
    from services.harness.helpers import _build_delegation_mcp_entry

    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: installed)
    profile = _copilot_cli_profile(
        {**_build_delegation_mcp_entry("copilot-cli"), "github": {"command": "npx", "args": ["-y", "gh-mcp"]}}
    )
    before = _frontmatter(profile)["mcp-servers"]["observal-agents"]
    assert (before["command"], before["args"][:2]) == ("python3", ["-m", "observal_cli.delegation.mcp_server"])
    out = cmd_pull.rewrite_frontmatter_hook_launchers(profile)
    assert out.split("\n---\n", 1)[1] == profile.split("\n---\n", 1)[1], "the instructions are untouched"
    servers = _frontmatter(out)["mcp-servers"]
    argv = [servers["observal-agents"]["command"], *servers["observal-agents"]["args"]]
    tail = ["-m", "observal_cli.delegation.mcp_server", "--harness", "copilot-cli"]
    if installed:
        assert argv == [sys.executable, "-I", *tail]
    else:
        assert argv == ["env", f"PYTHONPATH={launcher.package_root()}", sys.executable, "-P", *tail]
    assert servers["observal-agents"]["type"] == "stdio", "other fields of the entry are kept"
    assert servers["github"] == {"type": "stdio", "command": "npx", "args": ["-y", "gh-mcp"]}, "other servers kept"
    assert {k: v for k, v in _frontmatter(out).items() if k != "mcp-servers"} == {
        k: v for k, v in _frontmatter(profile).items() if k != "mcp-servers"
    }


@pytest.mark.parametrize("installed", [True, False])
def test_copilot_cli_profile_mcp_launcher_ignores_a_hostile_project_and_pythonpath(tmp_path, monkeypatch, installed):
    python = _installed_python(tmp_path) if installed else sys.executable
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: installed)
    monkeypatch.setattr(cmd_pull.sys, "executable", python)
    monkeypatch.setattr(launcher.sys, "executable", python)
    profile = _copilot_cli_profile({"observal-agents": {"command": "python3", "args": ["-m", _PROBE], "env": {}}})
    entry = _frontmatter(cmd_pull.rewrite_frontmatter_hook_launchers(profile))["mcp-servers"]["observal-agents"]
    project = _hostile_project(tmp_path / "project")
    hostile = {**_clean_env(project), "PYTHONPATH": str(project)}
    result = subprocess.run(
        [entry["command"], *entry["args"]], cwd=project, env=hostile, capture_output=True, check=False
    )
    assert _genuine(result), result.stderr[-200:]
    unsafe = subprocess.run(["python3", "-m", _PROBE], cwd=project, env=hostile, capture_output=True, check=False)
    assert b"HIJACKED" in unsafe.stdout, "the unrewritten launcher really imports the project"


def test_mcp_server_maps_in_a_damaged_profile_are_left_unchanged(monkeypatch):
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: True)
    broken = "---\nmcp-servers:\n  observal-agents:\n    command: python3\n    args: [-m, observal_cli.x\n---\nbody\n"
    assert cmd_pull.rewrite_frontmatter_hook_launchers(broken) == broken


def _pull_rewrites(snippet: dict, harness: str) -> dict:
    """The launcher rewrites agent pull applies before writing (pull_agent, write_install_snippet)."""
    from observal_cli.harness import ensure_loaded
    from observal_cli.harness import get_adapter as cli_adapter

    ensure_loaded()
    adapter = cli_adapter(harness)
    agent_id = "00000000-0000-4000-8000-000000000001"
    snippet = cmd_pull.rewrite_observal_interpreter(snippet)
    hooks = snippet.get("hooks_config")
    if isinstance(hooks, dict) and isinstance(hooks.get("content"), dict):
        hooks["content"] = adapter.rewrite_hooks(
            cmd_pull.rewrite_launchers_in_strings(hooks["content"]), agent_id=agent_id
        )
    profile = snippet.get("agent_profile")
    if isinstance(profile, dict) and isinstance(profile.get("content"), dict):
        profile["content"] = adapter.rewrite_agent_profile(profile["content"], agent_id=agent_id)
    elif isinstance(profile, dict) and isinstance(profile.get("content"), str):
        profile["content"] = cmd_pull.rewrite_frontmatter_hook_launchers(profile["content"])
    return snippet


_RULES = "Run python3 -m observal_cli.delegation.mcp_server only if asked."
_BARE = ("python3", "python")


def _bare_launchers(value, path=""):
    """Bare observal_cli launchers anywhere in generated output: in strings, MCP entries and argv.

    Frontmatter is parsed, so a launcher split across ``command:`` and ``args:``
    is found. Prose (the rules) and Windows-only ``powershell`` fields are not
    executed on POSIX and are skipped.
    """
    if isinstance(value, dict):
        args = value.get("args")
        if (
            value.get("command") in _BARE
            and isinstance(args, list)
            and args[:1] == ["-m"]
            and str([*args, ""][1]).startswith("observal_cli.")
        ):
            yield path, value
        for key, item in value.items():
            if key != "powershell":
                yield from _bare_launchers(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index in range(len(value) - 2):
            if value[index] in _BARE and value[index + 1] == "-m" and str(value[index + 2]).startswith("observal_cli."):
                yield path, value
        for index, item in enumerate(value):
            yield from _bare_launchers(item, f"{path}[{index}]")
    elif isinstance(value, str):
        if value.startswith("---\n") and "\n---" in value[4:]:
            head = value[4 : value.index("\n---", 4)]
            yield from _bare_launchers(yaml.safe_load(head), f"{path}#frontmatter")
            if cmd_pull._BARE_LAUNCHER.search(head.replace(_RULES, "")):
                yield f"{path}#frontmatter-text", head
        elif cmd_pull._BARE_LAUNCHER.search(value.replace(_RULES, "")):
            yield path, value


@pytest.mark.parametrize("installed", [True, False])
def test_no_harness_pull_output_keeps_a_bare_observal_cli_launcher(monkeypatch, installed):
    """Every server harness generator, through agent pull's rewrites, names no bare python3 launcher."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "observal-server"))
    from observal_shared.harness_registry import HARNESS_REGISTRY
    from services.harness import ConfigContext, ensure_loaded, get_adapter
    from services.harness.helpers import _build_delegation_mcp_entry

    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: installed)
    ensure_loaded()
    checked = []
    for harness in sorted(HARNESS_REGISTRY):
        adapter = get_adapter(harness)
        if adapter is None:
            continue
        hook = {"event": "Stop", "handler_type": "command", "handler_config": {"command": "make lint"}, "name": "lint"}
        agent = SimpleNamespace(
            id="00000000-0000-4000-8000-000000000001",
            name="reviewer",
            description="Reviews code.",
            model_name="",
            external_mcps=[],
            prompt=_RULES,
            components=[],
            version="1.0.0",
        )
        ctx = ConfigContext(
            agent=agent,
            safe_name="reviewer",
            harness=harness,
            observal_url="http://localhost:8000",
            mcp_configs=_build_delegation_mcp_entry(harness),
            rules_content=_RULES,
            hook_configs=[hook],
            options={"scope": "project"},
        )
        try:
            snippet = adapter.format_config(ctx)
        except Exception as error:
            pytest.fail(f"{harness}: format_config failed in isolation: {error!r}")
        snippet = _pull_rewrites(snippet, harness)
        found = list(_bare_launchers(snippet))
        assert not found, (harness, found[:2])
        checked.append(harness)
    assert {"claude-code", "copilot-cli", "kiro", "goose", "cursor"} <= set(checked)


# ── The CLI's own child processes ────────────────────────────────────────────


def _child_call_sites(tmp_path: Path, monkeypatch) -> dict[str, tuple[list[str], dict]]:
    """Run each internal observal_cli launch with subprocess stubbed, and capture its argv and env."""
    from observal_cli import cmd_auth
    from observal_cli.delegation import service, tasks
    from observal_cli.hooks import session_push

    captured: dict[str, tuple[list[str], dict]] = {}

    def recorder(name):
        def record(argv, **kwargs):
            captured[name] = (list(argv), dict(kwargs.get("env") or os.environ))
            return SimpleNamespace(pid=1, returncode=0, stdout="", stderr="")

        return record

    monkeypatch.setattr(session_push.subprocess, "Popen", recorder("session_push worker"))
    session_push._spawn_worker("--drain-outbox", harness="claude-code")

    monkeypatch.setattr(service, "SPAWN", None)
    monkeypatch.setattr(tasks, "task_dir", lambda task_id: tmp_path)
    monkeypatch.setattr(service.subprocess, "Popen", recorder("delegation runner"))
    service._spawn("task-1")

    monkeypatch.setattr(subprocess, "run", recorder("doctor patch after login"))
    cmd_auth._run_doctor_patch("cursor")
    return captured


def _as_probe(argv: list[str]) -> list[str]:
    """The same interpreter and options, with the module replaced by the probe."""
    return [*argv[: argv.index("-m")], "-m", _PROBE]


@pytest.mark.parametrize("installed", [True, False])
def test_internal_child_processes_ignore_a_hostile_project_and_pythonpath(tmp_path, monkeypatch, installed):
    """Regression: session-push workers, delegation runners and doctor patch ran ``sys.executable -m`` bare."""
    python = _installed_python(tmp_path) if installed else sys.executable
    project = _hostile_project(tmp_path / "project")
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: installed)
    monkeypatch.setattr(sys, "executable", python)
    monkeypatch.setenv("PYTHONPATH", str(project))  # inherited by the parent, as from a hostile shell
    sites = _child_call_sites(tmp_path, monkeypatch)
    monkeypatch.undo()
    assert set(sites) == {"session_push worker", "delegation runner", "doctor patch after login"}
    for name, (argv, env) in sites.items():
        assert argv[:2] == [python, "-I" if installed else "-P"], name
        if not installed:
            assert env["PYTHONPATH"] == launcher.package_root(), name
        result = subprocess.run(_as_probe(argv), cwd=project, env=env, capture_output=True, check=False)
        assert _genuine(result), (name, result.stdout, result.stderr[-200:])
    unsafe = subprocess.run(
        [python, "-m", _PROBE],
        cwd=project,
        env={**_clean_env(project), "PYTHONPATH": str(project)},
        capture_output=True,
        check=False,
    )
    assert b"HIJACKED" in unsafe.stdout, "the old bare form really imports the project"


def test_an_isolated_parent_launches_isolated_children_without_probing(tmp_path):
    python = _installed_python(tmp_path)
    project = _hostile_project(tmp_path / "project")
    script = (
        "import json; from observal_cli.shared import launcher; "
        "argv, env = launcher.module_subprocess('observal_cli.hooks.session_push', '--x'); "
        "print(json.dumps([argv, launcher.importable_in_isolation.cache_info().currsize]))"
    )
    out = subprocess.run(
        [python, "-I", "-c", script],
        cwd=project,
        env={**_clean_env(project), "PYTHONPATH": str(project)},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    argv, probes = json.loads(out)
    assert argv == [python, "-I", "-m", "observal_cli.hooks.session_push", "--x"]
    assert probes == 0, "no probe subprocess on the hook path"


def test_no_cli_source_launches_observal_cli_with_a_bare_interpreter():
    """Every ``[sys.executable, "-m", "observal_cli..."]`` list must go through launcher.module_subprocess."""
    import ast

    root = Path(launcher.package_root()) / "observal_cli"
    offenders = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if not isinstance(node, ast.List) or len(node.elts) < 3:
                continue
            first, second, third = node.elts[:3]
            if (
                ast.unparse(first) == "sys.executable"
                and isinstance(second, ast.Constant)
                and second.value == "-m"
                and isinstance(third, ast.Constant)
                and str(third.value).startswith("observal_cli")
            ):
                offenders.append(f"{path.relative_to(root.parent)}:{node.lineno}")
    assert not offenders, offenders


def _venv(path: Path, *, with_package: bool) -> str:
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(path)], check=True)
    python = str(path / "bin" / "python")
    if with_package:
        site = subprocess.run(
            [python, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        (Path(site) / "observal_cli_source.pth").write_text(launcher.package_root() + "\n")
    return python


@pytest.mark.parametrize("installed", [True, False])
def test_every_generated_launcher_works_with_spaces_in_its_paths(tmp_path, monkeypatch, installed):
    """Regression: four hook specs wrote the interpreter and PYTHONPATH unquoted, so a path
    with a space split into several shell words and the hook never ran."""
    python = _venv(tmp_path / "py dir" / "venv", with_package=installed)
    root = tmp_path / "Observal Flare"
    root.symlink_to(launcher.package_root(), target_is_directory=True)
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: installed)
    monkeypatch.setattr(launcher, "package_root", lambda: str(root))
    monkeypatch.setattr(sys, "executable", python)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    for name, command in _generated_commands().items():
        assert " dir/venv" in command, (name, command)
        result = subprocess.run(
            ["/bin/sh", "-c", _probe(command)], cwd=outside, env=_clean_env(outside), capture_output=True, check=False
        )
        assert _genuine(result), (name, command, result.stderr[-300:])


# ── Windows forms (run here under sh, as Git Bash would; cmd.exe needs a Windows reviewer) ──


def _windows_commands(agent_id: str = "a1") -> dict[str, str]:
    from observal_cli import cmd_doctor  # noqa: F401  (imported for the Cursor hook below)

    def first(output) -> str:
        values = [json.loads(f'"{raw}"') for raw in re.findall(r'"((?:[^"\\]|\\.)*)"', json.dumps(output))]
        return next(value for value in values if "observal_cli.hooks." in value)

    return {
        "claude-code": first(claude_code_hooks_spec.get_desired_hooks()),
        "codex": first(codex_hooks_spec.build_codex_hooks()),
        "kiro": kiro_hooks_spec.build_kiro_push_command(agent_id),
        "antigravity": first(antigravity_hooks_spec.build_antigravity_hooks()),
        "copilot-cli": copilot_cli_hooks_spec.build_copilot_cli_hooks(agent_id)["hooks"]["sessionStart"][0]["bash"],
        "cursor (doctor)": launcher.module_command("observal_cli.hooks.session_push"),
        "agent pull rewrite": cmd_pull.rewrite_launcher_command(
            "python3 -m observal_cli.hooks.session_push --harness x"
        ),
    }


def _as_windows_probe(command: str) -> str:
    """The same launcher with the module swapped for the probe (in -m or in the -c bootstrap)."""
    swapped = re.sub(r"observal_cli\.hooks\.[a-z_]+", _PROBE, command)
    assert swapped != command, command
    return swapped


@pytest.mark.parametrize("installed", [True, False])
def test_windows_hook_launchers_avoid_cmd_only_syntax_and_ignore_a_hostile_project(tmp_path, monkeypatch, installed):
    """Regression: Windows hooks used `set "PYTHONPATH=..." &&` (cmd.exe only; under Git Bash the
    variable is never set) and an unquoted interpreter that splits at C:\\Program Files."""
    python = _venv(tmp_path / "py dir" / "venv", with_package=installed)
    root = tmp_path / "Observal Flare"
    root.symlink_to(launcher.package_root(), target_is_directory=True)
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: installed)
    monkeypatch.setattr(launcher, "package_root", lambda: str(root))
    monkeypatch.setattr(sys, "executable", python)
    monkeypatch.setattr(sys, "platform", "win32")
    commands = _windows_commands()
    monkeypatch.setattr(sys, "platform", "darwin")  # run them here, under sh
    project = _hostile_project(tmp_path / "project")
    for name, command in commands.items():
        assert 'set "' not in command and "&&" not in command and "PYTHONPATH=" not in command, (name, command)
        assert command.startswith(f'"{python}" -I '), (name, command)
        result = _run_hostile(_as_windows_probe(command), project)
        assert _genuine(result), (name, command, result.stdout, result.stderr[-300:])


def test_windows_bootstrap_sets_the_agent_and_package_root_without_shell_quoting(monkeypatch):
    import runpy

    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: False)
    monkeypatch.setattr(launcher, "package_root", lambda: "C:\\Users\\a b\\Observal 'src'")
    argv = launcher.windows_module_argv("observal_cli.hooks.session_push", {"OBSERVAL_AGENT_ID": "a1%PATH%$x"})
    assert argv[1:3] == ["-I", "-c"]
    code = argv[3]
    assert not set(code) & set('%$`\\"!'), "nothing any shell would expand or unquote"
    called = []
    monkeypatch.setattr(runpy, "run_module", lambda module, **kwargs: called.append((module, kwargs)))
    monkeypatch.delenv("OBSERVAL_AGENT_ID", raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    exec(compile(code, "<bootstrap>", "exec"), {})
    assert os.environ["OBSERVAL_AGENT_ID"] == "a1%PATH%$x"
    assert sys.path[0] == "C:\\Users\\a b\\Observal 'src'"
    assert called == [("observal_cli.hooks.session_push", {"run_name": "__main__", "alter_sys": True})]
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: True)
    assert launcher.windows_module_argv("observal_cli.x") == [sys.executable, "-I", "-m", "observal_cli.x"]


@pytest.mark.parametrize("installed", [True, False])
def test_windows_argv_and_copilot_cli_profile_launchers_are_isolated(tmp_path, monkeypatch, installed):
    """Regression: on Windows a source checkout's argv launcher fell back to a bare interpreter,
    and Copilot CLI profile MCP launchers were not rewritten at all."""
    python = _venv(tmp_path / "py dir" / "venv", with_package=installed)
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: installed)
    monkeypatch.setattr(sys, "executable", python)
    monkeypatch.setattr(sys, "platform", "win32")
    argv = cmd_pull.rewrite_observal_interpreter(["claude", "mcp", "add", "x", "--", "python3", "-m", _PROBE, "--y"])
    profile = _copilot_cli_profile({"observal-agents": {"command": "python3", "args": ["-m", _PROBE], "env": {}}})
    entry = _frontmatter(cmd_pull.rewrite_frontmatter_hook_launchers(profile))["mcp-servers"]["observal-agents"]
    monkeypatch.setattr(sys, "platform", "darwin")
    launch = argv[argv.index("--") + 1 :]
    assert launch[:2] == [python, "-I"] and "env" not in argv
    assert [entry["command"], *entry["args"]][:2] == [python, "-I"]
    project = _hostile_project(tmp_path / "project")
    hostile = {**_clean_env(project), "PYTHONPATH": str(project)}
    for command in (launch, [entry["command"], *entry["args"]]):
        result = subprocess.run(command, cwd=project, env=hostile, capture_output=True, check=False)
        assert _genuine(result), (command, result.stderr[-300:])
