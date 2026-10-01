# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""The agent hook gate runs the original command exactly, only for its agent (POSIX)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from observal_cli.hook_gate import decide, gated_command

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the gate is POSIX-only")
INPUTS = Path(__file__).parent / "fixtures" / "component_insights" / "claude_code" / "hook_inputs"


def _input(agent_type: object = None, **extra) -> bytes:
    data = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash", **extra}
    if agent_type is not None:
        data["agent_type"] = agent_type
    return json.dumps(data).encode()


def _run(command: str, stdin: bytes, *, agent: str = "reviewer", cwd: Path | None = None, env=None, **options):
    """Run the generated settings command the way Claude Code does: /bin/sh -c."""
    gated = gated_command(agent, command, **options)
    return subprocess.run(
        ["/bin/sh", "-c", gated], input=stdin, capture_output=True, cwd=cwd, env=env, timeout=30, check=False
    )


def test_decision_follows_the_recorded_hook_inputs():
    """Every captured input: the agent's own events run, plain and other-agent events skip."""
    seen = set()
    for path in sorted(INPUTS.glob("*.json")):
        mode, _, event = path.stem.partition("--")
        raw = path.read_bytes()
        expected = "run" if mode.endswith("probe-agent") else "skip"
        assert decide(raw, "probe-agent") == expected, path.name
        assert decide(raw, "other-agent") == "skip", path.name
        seen.add(mode)
    assert {"headless-agent-probe-agent", "interactive-agent-probe-agent", "headless-plain"} <= seen


def test_unknown_inputs_are_not_treated_as_plain_sessions():
    for raw in (
        b"",
        b"not json",
        b"[]",
        json.dumps({"agent_type": "reviewer"}).encode(),
        _input(agent_type=""),
        _input(agent_type=7),
        _input(agent_type=None, session_id=3),
        json.dumps({"session_id": "", "hook_event_name": ""}).encode(),
        json.dumps({"session_id": "s", "hook_event_name": ""}).encode(),
    ):
        assert decide(raw, "reviewer") == "unknown", raw


def test_matching_agent_runs_with_exact_stdin_stdout_stderr_and_exit_code(tmp_path):
    stdin = _input("reviewer", tool_input={"command": "x\u00e9\x00y" * 3})
    for code in (0, 1, 2):
        result = _run(f"cat > {tmp_path}/in.bin; printf out; printf err >&2; exit {code}", stdin)
        assert (result.returncode, result.stdout, result.stderr) == (code, b"out", b"err")
        assert (tmp_path / "in.bin").read_bytes() == stdin


def test_partial_writes_are_retried_until_every_byte_is_delivered(monkeypatch):
    import observal_cli.hook_gate as gate

    read_fd, write_fd = os.pipe()
    real_write = os.write
    monkeypatch.setattr(gate.os, "write", lambda fd, data: real_write(fd, bytes(data[:3])))  # 3 bytes at a time
    gate._write_all(write_fd, b"exact bytes \x00\xff")
    os.close(write_fd)
    assert os.read(read_fd, 100) == b"exact bytes \x00\xff"


@pytest.mark.parametrize("size", [0, 1, 16_383, 16_384, 16_385, 70_000])
def test_inputs_around_pipe_capacities_arrive_intact_to_a_slow_reader(tmp_path, size):
    stdin = _input("reviewer", pad="p" * size)
    result = _run(f"sleep 0.3; cat > {tmp_path}/in.bin", stdin)
    assert result.returncode == 0 and (tmp_path / "in.bin").read_bytes() == stdin


def test_large_input_arrives_intact_and_unread_input_does_not_hang(tmp_path):
    stdin = _input("reviewer", tool_response="z" * 300_000)
    result = _run(f"cat > {tmp_path}/in.bin", stdin)
    assert result.returncode == 0 and (tmp_path / "in.bin").read_bytes() == stdin
    started = time.monotonic()
    assert _run("exit 0", stdin).returncode == 0  # never reads stdin
    assert time.monotonic() - started < 10


@pytest.mark.parametrize("agent_type", [None, "other-agent"])
def test_plain_and_other_agent_events_are_silent_and_never_run_the_command(tmp_path, agent_type):
    marker = tmp_path / "ran"
    result = _run(f"touch {marker}; echo visible; exit 2", _input(agent_type))
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")
    assert not marker.exists()


def test_unknown_input_skips_by_default_with_a_diagnostic_and_run_is_explicit(tmp_path):
    """Never run one agent's hook on input that cannot prove it is that agent's session."""
    marker = tmp_path / "ran"
    result = _run(f"touch {marker}; exit 2", b"not json")
    assert (result.returncode, result.stdout) == (0, b"") and not marker.exists()
    assert b"unrecognized hook input" in result.stderr
    assert _run("exit 2", b"not json", on_unknown="run").returncode == 2
    assert "--on-unknown" not in gated_command("a", "true")
    assert "--on-unknown run" in gated_command("a", "true", on_unknown="run")


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("printf 'a b' | tr a-z A-Z", b"A B"),
        ("X='it'\"'\"'s' sh -c 'printf %s \"$X\"'", b"it's"),
        ("false && echo no || printf yes", b"yes"),
        ("printf '%s' \"$(printf 'nested $(sub)')\"", b"nested $(sub)"),
        ("cat >/dev/null; printf '%s;%s' one two 2>/dev/null", b"one;two"),
        ("printf 'ünï\\tcode'", "ünï\tcode".encode()),
        ("cat | head -c 5", b'{"ses'),
    ],
)
def test_shell_heavy_commands_keep_their_original_semantics(command, expected):
    result = _run(command, _input("reviewer"))
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


def test_working_directory_and_environment_are_inherited(tmp_path):
    env = {**os.environ, "PROBE_VALUE": "kept 'quoted' $value"}
    result = _run('printf "%s|%s" "$(pwd -P)" "$PROBE_VALUE"', _input("reviewer"), cwd=tmp_path, env=env)
    assert result.stdout.decode() == f"{tmp_path.resolve()}|kept 'quoted' $value"


def test_signals_and_timeouts_reach_the_command_itself(tmp_path):
    """The gate execs the shell, so the hook process *is* the command: a kill or timeout leaves no orphan."""
    pid_file = tmp_path / "pid"
    root = str(Path(__file__).resolve().parents[1])
    proc = subprocess.Popen(
        [
            sys.executable,
            "-P",
            "-m",
            "observal_cli.hook_gate",
            "--agent",
            "reviewer",
            "--command",
            f"echo $$ > {pid_file}; exec sleep 30",
        ],
        stdin=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": root},
    )
    proc.stdin.write(_input("reviewer"))
    proc.stdin.close()
    deadline = time.monotonic() + 10
    while not pid_file.exists() or not pid_file.read_text().strip():
        assert time.monotonic() < deadline, "the command never started"
        time.sleep(0.05)
    assert int(pid_file.read_text()) == proc.pid, "the command runs in the hook process itself"
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=10) == -signal.SIGTERM


def test_the_gate_never_prints_or_stores_the_input_or_command(tmp_path):
    secret = "TOKEN-123-secret"
    result = _run("exit 0", _input("other-agent", tool_input={"command": secret}))
    assert secret.encode() not in result.stdout + result.stderr
    result = _run(f"exit 0 # {secret}", b"not json")
    assert secret.encode() not in result.stdout + result.stderr


def test_a_project_cannot_shadow_the_gate_from_its_working_directory(tmp_path):
    """Hooks run in the project directory; -P keeps a repository's observal_cli/ off sys.path."""
    fake = tmp_path / "observal_cli"
    fake.mkdir()
    (fake / "__init__.py").write_text("")
    (fake / "hook_gate.py").write_text("import sys; sys.stdout.write('HIJACKED'); sys.exit(0)")
    result = _run("printf genuine", _input("reviewer"), cwd=tmp_path)
    assert result.stdout == b"genuine"


def test_bad_arguments_fail_without_blocking():
    result = subprocess.run(
        [sys.executable, "-m", "observal_cli.hook_gate", "--agent", "x"], input=b"{}", capture_output=True, check=False
    )
    assert result.returncode == 1 and b"usage" in result.stderr


def test_generated_command_quotes_every_argument():
    command = gated_command("agent with space", "echo 'a' \"b\" $HOME; rm -rf /tmp/x && true")
    assert command.count("--command") == 1
    assert "'echo '\"'\"'a'\"'\"' \"b\" $HOME; rm -rf /tmp/x && true'" in command
    assert "'agent with space'" in command
    with pytest.raises(ValueError):
        gated_command("a", "true", on_unknown="maybe")
