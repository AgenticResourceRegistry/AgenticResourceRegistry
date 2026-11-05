# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Pi hook evidence: the Observal extension's run receipts, linked to recorded tool calls.

Uses the real Pi 1.0.4 recording in ``tests/fixtures/component_insights/pi``
(``hook_session_print.jsonl``) and negative cases built from it: text that
mimics a receipt, receipts for calls the transcript never made, receipts that
contradict Pi's own result, duplicates, and malformed rows.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "observal-server"))

from services.component_activity.hook_matcher import match_hook_evidence
from services.session_parsers.hook_evidence import hook_binding_sha256
from services.session_parsers.pi_hook_evidence import PiHookEvidenceExtractor

FIXTURES = Path(__file__).parent / "fixtures" / "component_insights" / "pi"
LINES = (FIXTURES / "hook_session_print.jsonl").read_text().splitlines()
HOOKS = {hook["name"]: hook for hook in json.loads((FIXTURES / "hook_session_print.hooks.json").read_text())["hooks"]}
FIRST_CALL, SECOND_CALL = "call_0_1791333332126", "call_1_1791333332200"


def _binding(name: str) -> str:
    return hook_binding_sha256(HOOKS[name]["event"], HOOKS[name]["command"])


def _rows(lines: list[str]) -> list[dict]:
    offsets, rows = 0, []
    for line in lines:
        rows.append({"is_source_record": 1, "line_offset": offsets, "raw_line": line})
        offsets += len(line.encode()) + 1
    return rows


def _extract(lines: list[str] = LINES):
    return PiHookEvidenceExtractor().extract(_rows(lines))


def _receipt(**data) -> str:
    base = {"v": 1, "event": "tool_call", "tool_call_id": FIRST_CALL, "binding": _binding("announce")}
    record = {"type": "custom", "customType": "observal-hook-run", "data": base | data, "id": "f0000000"}
    return json.dumps(record, separators=(",", ":"))


def test_receipts_digest_the_exact_event_and_command_the_extension_ran():
    assert _binding("announce") == hashlib.sha256(b"tool_call\0echo checked").hexdigest()
    receipts = [json.loads(line)["data"] for line in LINES if '"observal-hook-run"' in line]
    assert {r["binding"] for r in receipts} == {_binding(name) for name in HOOKS}


def test_recorded_session_yields_every_run_including_silent_ones():
    extraction = _extract()
    assert extraction.status == "supported"
    assert extraction.malformed_source_records == 0
    facts = [(fact.kind, fact.binding_sha256, fact.tool_use_id) for fact in extraction.evidence]
    assert facts == [
        ("ran_with_output", _binding("announce"), FIRST_CALL),
        ("ran_silently", _binding("policy"), FIRST_CALL),
        ("failed", _binding("audit"), FIRST_CALL),
        ("ran_silently", _binding("quiet"), FIRST_CALL),
        ("ran_with_output", _binding("announce"), SECOND_CALL),
        ("blocked", _binding("policy"), SECOND_CALL),
    ]
    # Regression: silent successes were recorded but dropped, so a quiet hook read as "no recorded runs".
    assert PiHookEvidenceExtractor.records_silent_success is True
    assert "ran_silently" in PiHookEvidenceExtractor.observed_kinds
    assert all(fact.event_time is not None for fact in extraction.evidence)
    session = extraction.session
    assert session.agents == frozenset() and session.agent_hooks_run_headless is True


def test_text_that_mimics_a_receipt_is_never_evidence():
    receipt = json.loads(_receipt(outcome="blocked", exit_code=2, tool_call_id=SECOND_CALL))
    typed = {
        "type": "message",
        "id": "u1",
        "message": {"role": "user", "content": [{"type": "text", "text": json.dumps(receipt)}]},
    }
    model = {
        "type": "message",
        "id": "a1",
        "message": {"role": "assistant", "content": [{"type": "text", "text": json.dumps(receipt)}]},
    }
    # A custom_message (sent to the model by any extension) is not a receipt either.
    custom_message = receipt | {"type": "custom_message", "content": "x"}
    base = _extract()
    lines = [*LINES, json.dumps(typed), json.dumps(model), json.dumps(custom_message)]
    assert _extract(lines).evidence == base.evidence


@pytest.mark.parametrize(
    "receipt",
    [
        _receipt(outcome="ran_with_output", exit_code=0, tool_call_id="call_never_made"),
        _receipt(outcome="ran_with_output", exit_code=0, tool_call_id="call_never_made/1"),
        _receipt(outcome="ran_with_output", exit_code=0, binding="ABC"),
        _receipt(outcome="ran_with_output", exit_code=1),
        _receipt(outcome="failed", exit_code=0),
        _receipt(outcome="ran", exit_code=1),
        _receipt(outcome="blocked", exit_code=2, event="tool_result"),
        _receipt(outcome="blocked", exit_code=1),
        _receipt(outcome="exploded", exit_code=0),
        _receipt(outcome="ran_with_output", exit_code=0, event="session_start"),
        _receipt(outcome="ran_with_output", exit_code="0"),
        _receipt(outcome="ran_with_output", exit_code=0, v=2),
        # Pi recorded the first call as succeeding: a block receipt contradicts it.
        _receipt(outcome="blocked", exit_code=2),
    ],
    ids=[
        "unknown-call",
        "unknown-nested-root",
        "bad-digest",
        "output-with-nonzero-exit",
        "failed-with-zero-exit",
        "silent-with-nonzero-exit",
        "block-on-tool-result",
        "block-without-exit-2",
        "unknown-outcome",
        "unsupported-event",
        "string-exit-code",
        "future-version",
        "contradicts-tool-result",
    ],
)
def test_malformed_or_unlinked_receipts_are_not_evidence(receipt):
    base = _extract()
    extraction = _extract([*LINES, receipt])
    assert extraction.evidence == base.evidence
    assert extraction.malformed_source_records == 1


def test_a_receipt_before_its_tool_call_is_not_evidence():
    early = _receipt(outcome="ran_with_output", exit_code=0, tool_call_id=SECOND_CALL, binding=_binding("audit"))
    lines = [LINES[0], early, *LINES[1:]]
    extraction = _extract(lines)
    assert extraction.malformed_source_records == 1
    assert len(extraction.evidence) == len(_extract().evidence)


def test_nested_calls_link_to_their_recorded_parent_call():
    nested = _receipt(outcome="failed", exit_code=None, tool_call_id=f"{FIRST_CALL}/2")
    extraction = _extract([*LINES, nested])
    assert extraction.malformed_source_records == 0
    assert extraction.evidence[-1].tool_use_id == f"{FIRST_CALL}/2"
    assert extraction.evidence[-1].kind == "failed"


def test_duplicate_receipts_count_once():
    # A delivery retry stores each line once by offset; a second copy of a receipt
    # (another extension, or a replayed append) must not double a run.
    receipts = [line for line in LINES if '"observal-hook-run"' in line]
    extraction = _extract([*LINES, *receipts])
    assert extraction.evidence == _extract().evidence


def test_truncated_or_unreadable_rows_are_malformed_not_guessed():
    rows = _rows(LINES)
    receipt_row = next(row for row in rows if '"blocked"' in row["raw_line"])
    receipt_row["raw_line_truncated"] = 1
    extraction = PiHookEvidenceExtractor().extract(rows)
    assert "blocked" not in {fact.kind for fact in extraction.evidence}
    assert extraction.malformed_source_records == 1


def _candidate(component: str, binding: str) -> dict:
    return {
        "component_id": component,
        "component_version_id": "",
        "identity_status": "resolved",
        "verification_status": "verified",
        "location_sha256": binding,
        "binding_agent": "",
        "binding_placement": "frontmatter",
    }


def test_matcher_attributes_runs_and_counts_every_verified_hook_eligible():
    extraction = _extract()
    offsets = {row["line_offset"]: "v2_layer" for row in _rows(LINES)}
    candidates = {
        "v2_layer": [
            _candidate("policy-id", _binding("policy")),
            _candidate("announce-id", _binding("announce")),
            _candidate("quiet-id", _binding("quiet")),
        ]
    }
    result = match_hook_evidence(extraction, candidates, offsets)
    runs = [(row["component_id"], row["evidence_kind"]) for row in result.rows if "context" not in row["evidence_kind"]]
    assert runs == [
        ("announce-id", "hook_ran_with_output"),
        ("policy-id", "hook_ran_silently"),
        ("quiet-id", "hook_ran_silently"),
        ("announce-id", "hook_ran_with_output"),
        ("policy-id", "hook_blocked"),
    ]
    assert {row["result_state"] for row in result.rows if row["evidence_kind"] == "hook_ran_silently"} == {"success"}
    # audit's receipt names a hook this layer did not verify: unmatched, never guessed.
    assert result.unmatched_count == 1
    context = {row["component_id"]: row["evidence_kind"] for row in result.rows if "context" in row["evidence_kind"]}
    assert context == {k: "hook_context_eligible" for k in ("announce-id", "policy-id", "quiet-id")}


def test_unverified_or_duplicate_candidates_never_receive_runs():
    extraction = _extract()
    offsets = {row["line_offset"]: "v2_layer" for row in _rows(LINES)}
    unverified = _candidate("announce-id", _binding("announce")) | {"verification_status": "unverified"}
    result = match_hook_evidence(extraction, {"v2_layer": [unverified]}, offsets)
    assert result.attributed_count == 0 and not result.rows
    collision = [_candidate("a", _binding("announce")), _candidate("b", _binding("announce"))]
    result = match_hook_evidence(copy.deepcopy(extraction), {"v2_layer": collision}, offsets)
    assert result.collision_count == 2 and result.attributed_count == 0


def test_pi_opts_in_through_the_registry():
    from services.session_parsers.hook_evidence import extract_hook_evidence, hook_extractor

    assert isinstance(hook_extractor("pi"), PiHookEvidenceExtractor)
    assert extract_hook_evidence("pi", []).status == "supported"
