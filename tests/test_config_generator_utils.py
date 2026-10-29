# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import json
import tomllib

import pytest

from observal_cli.cmd_pull import _dict_to_toml, _write_file
from observal_cli.config import migrate_shimmed_mcp_configs
from observal_cli.shared.utils import extract_mcp_servers as _parse_project_mcp_servers


def test_dict_to_toml():
    d = {"mcp.servers": {"my-server": {"command": "npx", "args": ["a", "b"], "env": {"K": "V"}}}}
    toml = _dict_to_toml(d)
    assert "[mcp.servers.my-server]" in toml
    assert 'command = "npx"' in toml
    assert 'args = ["a", "b"]' in toml
    assert 'env.K = "V"' in toml


def test_parse_project_mcp_servers():
    codex_conf = {"mcp": {"servers": {"c-serv": {}}}}
    copilot_conf = {"servers": {"cp-serv": {}}}
    opencode_conf = {"mcp": {"o-serv": {}}}

    assert _parse_project_mcp_servers(codex_conf, "codex") == {"c-serv": {}}
    assert _parse_project_mcp_servers(copilot_conf, "copilot") == {"cp-serv": {}}
    assert _parse_project_mcp_servers(opencode_conf, "opencode") == {"o-serv": {}}


def test_write_file_merge_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[mcp.servers.old]\ncommand = 'echo'\n")

    content = {"mcp.servers": {"new": {"command": "node"}}}
    res = _write_file(p, content, merge_mcp=True)
    assert res == "merged"

    merged = p.read_text()
    assert "[mcp.servers.old]" in merged
    assert "[mcp.servers.new]" in merged
    assert 'command = "node"' in merged


def test_migrate_standard_wrapped_json(tmp_path):
    path = tmp_path / ".cursor/mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "server": {
                        "command": "observal-shim",
                        "args": ["--mcp-id", "abc", "--", "npx", "-y", "server"],
                        "env": {"TOKEN": "value"},
                    }
                }
            }
        )
    )

    assert migrate_shimmed_mcp_configs(home=tmp_path, cwd=tmp_path) == [path]
    entry = json.loads(path.read_text())["mcpServers"]["server"]
    assert entry == {"command": "npx", "args": ["-y", "server"], "env": {"TOKEN": "value"}}
    assert path.with_suffix(".json.pre-unshim.bak").exists()


def test_migrate_opencode_command_array(tmp_path):
    path = tmp_path / "opencode.json"
    path.write_text(
        json.dumps(
            {
                "mcp": {
                    "server": {
                        "type": "local",
                        "command": ["observal-shim", "--mcp-id", "abc", "--", "uvx", "server"],
                    }
                }
            }
        )
    )

    migrate_shimmed_mcp_configs(home=tmp_path / "home", cwd=tmp_path)
    entry = json.loads(path.read_text())["mcp"]["server"]
    assert entry["command"] == ["uvx", "server"]


def test_migrate_wrapped_toml(tmp_path):
    path = tmp_path / ".codex/config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        '# User settings must survive migration.\nmodel = "gpt-5"\n\n'
        '[mcp_servers.server]\ncommand = "observal-shim"\nargs = ["--mcp-id", "abc", "--", "node", "server.js"]\n'
    )

    migrate_shimmed_mcp_configs(home=tmp_path, cwd=tmp_path)
    rendered = path.read_text()
    entry = tomllib.loads(rendered)["mcp_servers"]["server"]
    assert entry["command"] == "node"
    assert entry["args"] == ["server.js"]
    assert '# User settings must survive migration.\nmodel = "gpt-5"' in rendered


def test_malformed_wrapped_config_fails_loudly(tmp_path):
    path = tmp_path / ".cursor/mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"broken": {"command": "observal-shim", "args": []}}}))

    with pytest.raises(RuntimeError, match="has no command delimiter"):
        migrate_shimmed_mcp_configs(home=tmp_path, cwd=tmp_path)
