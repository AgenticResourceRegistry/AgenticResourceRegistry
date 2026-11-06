# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Bindings, user mentions and installed files do not attest loaded instructions."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.insights.cohort import qualified_cache, select_cohort
from services.insights.scope import SessionKey
from services.session_parsers import instruction_evidence

PROMPT = "Write one focused candidate test. Never execute it."
EXPECTED = {"prompt": PROMPT, "version_id": "v1", "names": ["owner/tester", "tester"]}


def message(role, content="", **fields):
    """Build one Pi fixture message with a stable tree identity and optional parent."""
    return {"type": "message", "message": {"role": role, "content": content, **fields}}


def evidence(entries, expected=EXPECTED, harness="pi"):
    """Evaluate fixture instruction receipts against the selected role contract."""
    return instruction_evidence(harness, [json.dumps(e) for e in entries], expected)


def system(prompt=PROMPT):
    """Build a recorded system instruction checkpoint, not a conversation mention."""
    return message(
        "system",
        sections={
            "preamble": "Base harness instructions",
            "project_context": f'<project_instructions path="/home/test/.pi/agent/AGENTS.md">\n{prompt}\n</project_instructions>',
        },
    )


def test_system_prompt_is_verified_without_logging_instruction_body():
    """Verify system prompt is verified without logging instruction body."""
    result = evidence([system(), message("user", "Find a reproducer"), message("assistant", [])])
    assert result["state"] == "verified" and result["responses_checked"] == 1
    assert PROMPT not in json.dumps(result)


@pytest.mark.parametrize("role", ["user", "assistant", "toolResult"])
def test_prompt_in_conversation_text_does_not_prove_profile_load(role):
    """Verify prompt in conversation text does not prove profile load."""
    assert evidence([message(role, PROMPT), message("assistant", [])])["state"] == "unknown"


def test_installed_skill_descriptions_do_not_prove_agent_prompt_load():
    """Verify installed skill descriptions do not prove agent prompt load."""
    rows = [message("system", sections={"preamble": "Base", "skills": PROMPT}), message("assistant", [])]
    assert evidence(rows)["state"] == "mismatch"


def test_explicit_missing_or_changed_prompt_is_mismatch():
    """Verify explicit missing or changed prompt is mismatch."""
    assert evidence([system("Different agent instructions"), message("assistant", [])])["state"] == "mismatch"
    assert (
        evidence(
            [
                system(),
                message("assistant", []),
                message("system", sections={"project_context": None}),
                message("assistant", []),
            ]
        )["state"]
        == "mismatch"
    )


def test_legacy_compaction_without_checkpoint_is_unknown():
    """Verify legacy compaction without checkpoint is unknown."""
    assert (
        evidence(
            [system(), message("assistant", []), {"type": "compaction", "summary": PROMPT}, message("assistant", [])]
        )["state"]
        == "unknown"
    )
    checkpoint = {"type": "compaction", "summary": "summary", "systemMessage": system()["message"]}
    assert evidence([system(), message("assistant", []), checkpoint, message("assistant", [])])["state"] == "verified"


def test_patch_of_unrelated_section_preserves_loaded_instructions():
    """Verify patch of unrelated section preserves loaded instructions."""
    assert (
        evidence(
            [system(), message("system", sections={"skills": "new skill descriptions"}), message("assistant", [])]
        )["state"]
        == "verified"
    )


def test_every_branch_response_is_checked_not_only_last_branch():
    """Verify every branch response is checked not only last branch."""
    rows = [
        dict(system(), id="s", parentId=None),
        dict(message("assistant", []), id="a", parentId="s"),
        dict(system("Wrong instructions"), id="other", parentId="s"),
        dict(message("assistant", []), id="bad", parentId="other"),
        dict(message("assistant", []), id="final", parentId="s"),
    ]
    assert evidence(rows)["state"] == "mismatch"


@pytest.mark.parametrize(
    "rows",
    [
        [dict(system(), id="s", parentId="absent"), dict(message("assistant", []), id="a", parentId="s")],
        [dict(system(), id="s", parentId="a"), dict(message("assistant", []), id="a", parentId="s")],
        [dict(system(), id="s", parentId=None), dict(message("assistant", []), id="s", parentId=None)],
    ],
)
def test_missing_cyclic_and_duplicate_trees_remain_unknown(rows):
    """Verify missing cyclic and duplicate trees remain unknown."""
    assert evidence(rows)["state"] == "unknown"


def test_delegation_requires_exact_role_and_identity_envelope():
    """Verify delegation requires exact role and identity envelope."""
    text = f"You are acting as the agent `owner/tester`. Follow its instructions.\n\n<agent-instructions>\n{PROMPT}\n</agent-instructions>\n\n<task>Find a reproducer</task>"
    assert evidence([message("user", text), message("assistant", [])])["state"] == "verified"
    assert evidence([message("user", "Example: " + text), message("assistant", [])])["state"] == "unknown"
    assert (
        evidence([message("user", text.replace("owner/tester", "different")), message("assistant", [])])["state"]
        == "unknown"
    )


def test_source_and_expected_version_changes_invalidate_facet_marker():
    """Verify source and expected version changes invalidate facet marker."""
    rows = [system(), message("assistant", [])]
    first = evidence(rows)["cache_key"]
    assert first != evidence([*rows, message("user", "more")])["cache_key"]
    assert first != evidence(rows, {**EXPECTED, "version_id": "v2"})["cache_key"]
    key = SessionKey("p", "u", "pi", "s")
    metas = [{"session_key": key, "instruction_evidence": {"cache_key": first}}]
    assert not qualified_cache({key: {"brief_summary": "legacy"}}, metas)
    assert key in qualified_cache({key: {"_cohort_key": first}}, metas)


@pytest.mark.parametrize(
    "harness", ["claude-code", "kiro", "cursor", "copilot", "copilot-cli", "goose", "opencode", "antigravity"]
)
def test_formats_without_instruction_receipts_are_explicitly_unknown(harness):
    """Verify formats without instruction receipts are explicitly unknown."""
    assert evidence([system(), message("assistant", [])], harness=harness)["state"] == "unknown"


def test_parent_child_units_and_costs_do_not_collapse_users():
    """Verify parent child units and costs do not collapse users."""

    def meta(user, sid, state="verified", parent="", known=True, failed=False):
        """Build a scoped session fixture with explicit instruction/relevance coverage."""
        return {
            "session_key": SessionKey("p", user, "pi", sid),
            "instruction_evidence": {"state": state, "reason": "fixture"},
            "parent_session_id": parent,
            "parent_metadata_known": known,
            "failed_run": failed,
            "user_message_count": 2,
            "total_cost": 3,
        }

    selected, coverage = select_cohort(
        [
            meta("alice", "same"),
            meta("alice", "child", parent="same"),
            meta("bob", "orphan", parent="same"),
            meta("bob", "unknown", "unknown"),
            meta("alice", "changed", "mismatch"),
            meta("alice", "unscoped", known=False),
            meta("alice", "failed", failed=True),
        ]
    )
    assert [m["session_key"].session_id for m in selected] == ["same"]
    assert coverage["child_sessions"] == 2 and coverage["decisions"]["child_parent_unresolved"] == 1
    assert coverage["bound_operational"]["top_level_reported_cost_usd"] == 12
    assert coverage["bound_operational"]["child_reported_cost_usd"] == 6


@pytest.mark.asyncio
async def test_empty_verified_cohort_never_calls_model(monkeypatch):
    """Verify empty verified cohort never calls model."""
    from services.insights import generator

    key = SessionKey("p", "u", "pi", "s")
    monkeypatch.setattr(
        generator,
        "extract_all_session_metas",
        AsyncMock(
            return_value=[
                {
                    "session_key": key,
                    "parent_metadata_known": True,
                    "instruction_evidence": {"state": "unknown", "reason": "legacy"},
                }
            ]
        ),
    )
    model = AsyncMock()
    monkeypatch.setattr(generator, "generate_sections", model)
    result = await generator.generate_report_content(
        "tester", "agent", agent_config={}, db=SimpleNamespace(close=AsyncMock())
    )
    assert result["sessions_analyzed"] == 0 and result["metrics"]["cohort"]["bound_sessions"] == 1
    assert not result["narrative"]["suggestions"]["config_additions"]
    model.assert_not_awaited()


def test_codex_delegated_role_uses_existing_converter_not_a_fidelity_rewrite():
    """Verify codex delegated role uses existing converter not a fidelity rewrite."""
    text = f"You are acting as the agent `tester`. Follow its instructions.\n\n<agent-instructions>\n{PROMPT}\n</agent-instructions>\n\n<task>Find a reproducer</task>"
    rows = [
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "candidate"}],
            },
        },
    ]
    assert evidence(rows, harness="codex")["state"] == "verified"
    assert evidence(rows[:1], harness="codex")["state"] == "unknown"


def test_private_instruction_contract_is_never_sent_as_config_data():
    """Verify private instruction contract is never sent as config data."""
    from services.insights.generator import _build_data_block

    result = _build_data_block(
        "tester",
        {},
        {},
        [],
        "start",
        "end",
        agent_config={
            "system_prompt_excerpt": "public role",
            "_instruction_contract": {"prompt": "private-full-contract"},
        },
    )
    assert "public role" in result and "private-full-contract" not in result and "_instruction_contract" not in result


def test_repo_docs_inactive_profiles_and_code_quotes_are_not_active_agent_evidence():
    """Verify repo docs inactive profiles and code quotes are not active agent evidence."""
    for path in ["/repo/AGENTS.md", "/home/test/.pi/agent/agents/tester/AGENTS.md"]:
        rows = [
            message(
                "system",
                sections={
                    "preamble": "Base",
                    "project_context": f'<project_instructions path="{path}">{PROMPT}</project_instructions>',
                },
            ),
            message("assistant", []),
        ]
        assert evidence(rows)["state"] != "verified"
    assert evidence([system("```\n" + PROMPT + "\n```"), message("assistant", [])])["state"] != "verified"


def test_delegation_parser_preserves_literal_closing_tags_and_crlf():
    """Verify delegation parser preserves literal closing tags and crlf."""
    from services.session_parsers.pi_instructions import _delegated_prompt

    role = "Explain </agent-instructions> as literal text."
    text = f"You are acting as the agent `tester`. Follow its instructions.\r\n<agent-instructions>\r\n{role}\r\n</agent-instructions>\r\n<task>Explain </agent-instructions> and </task> tags</task>\r\n"
    assert _delegated_prompt(text, role, ["tester"])
    assert not _delegated_prompt(text, role + " extra", ["tester"])
    assert not _delegated_prompt(text.replace("</task>\r\n", ""), role, ["tester"])


def test_delegation_missing_close_is_bounded_on_large_whitespace():
    """Verify delegation missing close is bounded on large whitespace."""
    import subprocess
    import sys
    from pathlib import Path

    file = Path(__file__).parents[1] / "observal-server/services/session_parsers/pi_instructions.py"
    script = "import runpy,sys; m=runpy.run_path(sys.argv[1]); assert not m['_delegated_prompt']('You are acting as the agent `tester`. Follow its instructions.<agent-instructions>'+' '*200000, 'role', ['tester'])"
    subprocess.run([sys.executable, "-c", script, str(file)], check=True, timeout=10)


def test_provider_metrics_drop_all_nested_rosters_without_mutating_records():
    """Verify provider metrics drop all nested rosters without mutating records."""
    import copy

    from services.insights.cohort import prompt_metrics

    roster = [{"user_id": "private-user", "session_id": "private-session"}]
    metrics = {
        "metrics": {
            "cohort": {"members": roster, "bound_sessions": 1},
            "rich": {
                "version_comparison_baseline": {"prior_cohort": {"members": roster, "verified_top_level_sessions": 1}}
            },
        }
    }
    before = copy.deepcopy(metrics)
    public = prompt_metrics(metrics)
    assert metrics == before
    assert "members" not in json.dumps(public) and "private-user" not in json.dumps(public)
    assert public["metrics"]["cohort"]["bound_sessions"] == 1
    assert public["metrics"]["rich"]["version_comparison_baseline"]["prior_cohort"]["verified_top_level_sessions"] == 1
