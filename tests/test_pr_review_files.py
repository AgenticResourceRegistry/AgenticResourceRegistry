# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Virtual files, redaction, real hunks and comment re-anchoring."""

from types import SimpleNamespace

import pytest

from models.mcp import McpVersion
from services.review.anchors import reanchor
from services.review.checks import permission_delta, snapshot_checks
from services.review.diff import diff_files
from services.review.files import FIELDS, render_component


def version(kind, **kwargs):
    data = dict.fromkeys(FIELDS[kind])
    data.update(
        {
            "skill_md_content": "# Readme\n",
            "script_content": None,
            "script_filename": None,
            "tools_schema": None,
            "setup_instructions": None,
            "template": "Hi\n",
        }
    )
    data.update(kwargs)
    return SimpleNamespace(**data)


@pytest.mark.parametrize("kind", ["mcp", "skill", "hook", "prompt", "sandbox"])
def test_all_component_renderers(kind):
    assert f"{kind}.yaml" in render_component(kind, version(kind))


def test_redaction_nested_and_untrusted_script_path():
    files = render_component(
        "skill",
        version(
            "skill",
            mcp_server_config={"env": {"MY_TOKEN": "shh"}},
            script_filename="../../etc/passwd",
            script_content="echo hello",
        ),
    )
    assert "shh" not in str(files)
    assert "<redacted>" in files["skill.yaml"]["content"]
    assert "scripts/script.sh" in files
    assert all(".." not in key for key in files)
    mcp = render_component(
        "mcp",
        version(
            "mcp",
            environment_variables=[{"name": "API_KEY", "value": "secret"}],
            headers=[{"name": "Authorization", "value": "bearer secret"}],
        ),
    )
    assert "secret" not in str(mcp)
    env = render_component(
        "sandbox", version("sandbox", runtime_config={"env": {"DB_PASS": "hunter2"}, "max_tokens": 5})
    )
    assert "hunter2" not in str(env)
    assert "max_tokens: 5" in env["sandbox.yaml"]["content"]
    assert "API_KEY" in mcp["mcp.yaml"]["content"]


def test_diff_tracks_insertions_without_repainting_remaining_lines():
    base = {"x.md": {"content": "one\ntwo\nthree\n", "lang": "markdown"}}
    head = {"x.md": {"content": "one\nNEW\ntwo\nthree\n", "lang": "markdown"}}
    diff = diff_files(base, head)[0]
    assert diff["additions"] == 1 and diff["deletions"] == 0
    assert [(line["t"], line.get("h")) for line in diff["hunks"][0]["lines"]] == [
        ("ctx", 1),
        ("add", 2),
        ("ctx", 3),
        ("ctx", 4),
    ]
    assert diff_files({}, head)[0]["status"] == "added"
    assert diff_files(base, {})[0]["status"] == "removed"
    renamed = diff_files(base, {"renamed.md": base["x.md"]})[0]
    assert renamed["status"] == "renamed" and renamed["old_path"] == "x.md"


def test_large_file_requires_explicit_load():
    head = {"big": {"content": "x" * 400_001, "lang": "text"}}
    assert diff_files({}, head)[0]["too_large"]
    assert diff_files({}, head, full=True)[0]["additions"] == 1


def test_anchors_move_and_become_outdated():
    old = {"a.md": {"content": "one\ntwo\nthree\n"}}
    new = {"a.md": {"content": "first\none\ntwo\nreplaced\n"}}
    moved = SimpleNamespace(path="a.md", side="head", start_line=2, end_line=2, outdated=False)
    outdated = SimpleNamespace(path="a.md", side="head", start_line=3, end_line=3, outdated=False)
    assert reanchor([moved, outdated], old, new) == (1, 1)
    assert (moved.start_line, moved.end_line) == (3, 3)
    assert outdated.outdated


def test_provenance_warning_and_required_fields():
    version = McpVersion(
        description="MCP", version="1.0.0", url="https://example.test/mcp", source_url="https://example.test/repo"
    )
    checks = snapshot_checks(render_component("mcp", version), version, subject_type="mcp")
    assert next(c for c in checks if c["id"] == "provenance")["status"] == "warn"
    assert next(c for c in checks if c["id"] == "schema")["status"] == "pass"
    assert not any(c["id"] == "mcp_validation" for c in checks)  # evaluated live by the gate


def test_description_is_reviewed_and_benign_names_are_not_redacted():
    files = render_component("skill", version("skill", description="Triage issues", activation_keywords=["gh"]))
    assert "Triage issues" in files["skill.yaml"]["content"]
    assert "gh" in files["skill.yaml"]["content"]
    assert render_component("skill", version("skill", description="a")) != render_component(
        "skill", version("skill", description="b")
    )


def test_permission_delta_compares_values_not_key_names():
    base = render_component("hook", version("hook", event="PreToolUse", scope="agent", description="a"))
    renamed = render_component("hook", version("hook", event="PreToolUse", scope="agent", description="b"))
    widened = render_component("hook", version("hook", event="PreToolUse", scope="global", description="a"))
    assert permission_delta(base, renamed) == []
    assert permission_delta(base, widened) == ["hook.yaml:scope"]


def test_secret_check_in_free_text():
    token = "ghp_" + "a" * 36
    assert snapshot_checks({"x": {"content": token}})[0]["status"] == "fail"
    files = render_component("skill", version("skill", skill_md_content=f"# Secret: {token}"))
    assert token not in str(files)
    assert "<redacted>" in files["SKILL.md"]["content"]


@pytest.mark.parametrize(
    "token",
    [
        "sk-proj-abcdefghijklmnopqrstuvwxyz123456",
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz_1234",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "github_pat_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH",
        "AKIAABCDEFGHIJKLMNOP",
        "xoxb-1234567890-abcdefghij",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_credential_scan_covers_modern_token_shapes(token):
    from services.review.checks import CREDENTIAL

    assert CREDENTIAL.search(f"export KEY={token}")
