# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Version-scoped, sanitized native Codex skills inventory and install proof."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from observal_cli.harness.codex import CodexAdapter
from observal_cli.layer import _skill_status, build_layer_manifest, skill_location_sha256, skill_verification_entry
from services.component_activity import skill_queries
from services.component_activity.skill_matcher import match_skill_evidence
from services.session_parsers.skill_evidence import extract_skill_evidence, kind_recorded

FIXTURE = Path(__file__).parent / "fixtures/component_insights/codex/skill_available.jsonl"
SELECTED = Path(__file__).parent / "fixtures/component_insights/codex/skill_selected.jsonl"
ALIAS = "matrix-probe-skill"


def rows():
    return [
        {"is_source_record": 1, "line_offset": index, "raw_line": raw, "line_hash": f"hash-{index}"}
        for index, raw in enumerate(FIXTURE.read_text().splitlines())
    ]


def selected_rows():
    return [
        {"is_source_record": 1, "line_offset": index, "raw_line": raw, "line_hash": f"selected-hash-{index}"}
        for index, raw in enumerate(SELECTED.read_text().splitlines())
    ]


def user_rows(*, selected=False):
    """Sanitized Codex user-root shape, never raw private session content."""
    project_root = "/private/tmp/observal-codex-proof/.agents/skills"
    user_root = "/private/tmp/inert-codex-home/.agents/skills"
    source = selected_rows() if selected else rows()
    return [dict(row, raw_line=row["raw_line"].replace(project_root, user_root)) for row in source]


def changed(rows_, index, change):
    items = [dict(row) for row in rows_]
    raw = json.loads(items[index]["raw_line"])
    change(raw)
    items[index]["raw_line"] = json.dumps(raw)
    return items


def with_combined_context(source):
    """Sanitized native 0.163.0 AGENTS.md + environment record before the typed turn."""
    context = json.loads(source[2]["raw_line"])
    context["payload"]["id"] = "synthetic-combined-context"
    context["payload"]["internal_chat_message_metadata_passthrough"]["content_item_kinds"] = [
        "agents_md.instructions",
        "environments.environment_context",
    ]
    context["payload"]["content"] = [
        {"type": "input_text", "text": "Inert project instructions."},
        {"type": "input_text", "text": "Inert environment context."},
    ]
    items = [*source[:2], {"is_source_record": 1, "raw_line": json.dumps(context)}, *source[2:]]
    return [dict(row, line_offset=i, line_hash=f"combined-hash-{i}") for i, row in enumerate(items)]


def test_native_inventory_availability_does_not_infer_invocation_from_user_prompt():
    extracted = extract_skill_evidence("codex", rows())
    assert extracted.status == "supported"
    assert extracted.malformed_source_records == 0
    assert len(extracted.evidence) == 1
    fact = extracted.evidence[0]
    assert (fact.kind, fact.scope, fact.alias, fact.source_line_offset) == ("available", "project", ALIAS, 1)
    assert (
        fact.location_sha256
        == hashlib.sha256(f"/private/tmp/observal-codex-proof/.agents/skills/{ALIAS}/SKILL.md".encode()).hexdigest()
    )
    assert "tmp" not in str(fact)
    assert kind_recorded("codex", "available")
    assert not kind_recorded("codex", "load")
    assert kind_recorded("codex", "invoked")
    assert kind_recorded("codex", "invoked", "project")
    assert kind_recorded("codex", "invoked", "user")
    assert kind_recorded("codex", "available", "user")
    assert kind_recorded("claude-code", "invoked", "user")
    assert kind_recorded("pi", "available", "user")
    assert extract_skill_evidence("kiro", rows()).status == "unsupported"


def test_unrelated_user_root_cannot_make_verified_project_pin_a_no_selection():
    # Codex can complete a native inventory without advertising the pinned
    # project's root. This source is supported for the user skill, not an
    # observed zero for a different project skill with the same alias.
    other_root = changed(
        rows(),
        1,
        lambda record: record["payload"]["content"][0].update(
            text=record["payload"]["content"][0]["text"].replace(
                "/observal-codex-proof/.agents/skills", "/other-user/.agents/skills"
            )
        ),
    )
    extracted = extract_skill_evidence("codex", other_root)
    assert extracted.status == "supported"
    assert [(fact.kind, fact.scope) for fact in extracted.evidence] == [("available", "user")]
    project = extract_skill_evidence("codex", rows()).evidence[0]
    verified_project_pin = {
        "component_id": "listing",
        "component_version_id": "version",
        "local_name": ALIAS,
        "scope": "project",
        "location_sha256": project.location_sha256,
        "identity_status": "resolved",
        "verification_status": "verified",
    }
    attribution = match_skill_evidence(extracted.evidence, {"v2": [verified_project_pin]}, {1: "v2"})
    assert attribution.rows == () and attribution.unmatched_count == 1
    now = datetime.now(UTC)
    params = skill_queries._params(
        "default", "11111111-1111-4111-8111-111111111111", None, (now - timedelta(days=1), now)
    )
    assert "codex" in params["param_available_capable"]
    # Even with a complete skill publication, a verified project install is
    # not measurable unless this *listing* has an attributed native offer.
    assert (
        "AND (NOT has({available_capable:Array(String)}, c.harness)\n"
        "                     OR ifNull(e.available_facts, 0) > 0))\n"
        "            OR (ifNull(scope_map.user_installs, 0) > 0"
    ) in skill_queries._SKILL_SESSIONS


def test_native_selected_skill_has_tagged_project_origin_not_a_file_read():
    result = extract_skill_evidence("codex", selected_rows())
    assert result.status == "supported"
    assert [(e.kind, e.scope, e.alias, e.source_line_offset) for e in result.evidence] == [
        ("available", "project", ALIAS, 1),
        ("invoked", "project", ALIAS, 3),
    ]
    assert result.evidence[1].location_sha256 == result.evidence[0].location_sha256
    assert "SKILL.md" not in str(result.evidence[1])


def test_native_combined_project_context_is_neither_selection_nor_unknown_when_well_formed():
    available = extract_skill_evidence("codex", with_combined_context(rows()))
    assert available.status == "supported"
    assert [(e.kind, e.source_line_offset) for e in available.evidence] == [("available", 1)]
    selected = extract_skill_evidence("codex", with_combined_context(selected_rows()))
    assert selected.status == "supported"
    assert [(e.kind, e.source_line_offset) for e in selected.evidence] == [("available", 1), ("invoked", 4)]
    assert selected.evidence[1].location_sha256 == selected.evidence[0].location_sha256


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["payload"]["internal_chat_message_metadata_passthrough"].update(
            content_item_kinds=["environments.environment_context", "agents_md.instructions"]
        ),
        lambda r: r["payload"]["internal_chat_message_metadata_passthrough"].update(
            content_item_kinds=["agents_md.instructions", "environments.environment_context", "user.text"]
        ),
        lambda r: r["payload"]["internal_chat_message_metadata_passthrough"].update(
            content_item_kinds=["agents_md.instructions", "skills.selected_skill_instructions"]
        ),
        lambda r: r["payload"]["internal_chat_message_metadata_passthrough"].update(turn_id="other-turn"),
        lambda r: r["payload"].update(content=[r["payload"]["content"][0]]),
        lambda r: r["payload"]["content"][1].update(type="output_text"),
        lambda r: r["payload"]["content"][1].update(text=""),
    ],
)
def test_spoofed_or_incomplete_combined_context_is_unsupported(mutation):
    assert (
        extract_skill_evidence("codex", changed(with_combined_context(selected_rows()), 2, mutation)).status
        == "unsupported"
    )


def test_skill_markup_in_agents_context_is_not_a_native_selection():
    pasted = changed(
        with_combined_context(rows()),
        2,
        lambda record: record["payload"]["content"][0].update(text="<skill><name>matrix-probe-skill</name></skill>"),
    )
    result = extract_skill_evidence("codex", pasted)
    assert result.status == "supported"
    assert [fact.kind for fact in result.evidence] == ["available"]


def test_combined_context_must_precede_its_typed_turn_and_not_repeat():
    source = with_combined_context(selected_rows())
    after_prompt = [*source[:2], source[3], source[2], *source[4:]]
    after_prompt = [dict(row, line_offset=i) for i, row in enumerate(after_prompt)]
    assert extract_skill_evidence("codex", after_prompt).status == "unsupported"
    duplicate = [*source[:3], source[2], *source[3:]]
    assert (
        extract_skill_evidence("codex", [dict(row, line_offset=i) for i, row in enumerate(duplicate)]).status
        == "unsupported"
    )
    missing_prompt = [dict(row, line_offset=i) for i, row in enumerate(source[:3] + source[4:])]
    assert extract_skill_evidence("codex", missing_prompt).status == "unsupported"

    # Re-tagging a native selection as context cannot silently convert it to zero.
    def retag_selection(record):
        record["payload"]["internal_chat_message_metadata_passthrough"]["content_item_kinds"] = [
            "agents_md.instructions",
            "environments.environment_context",
        ]
        record["payload"]["content"] = [
            {"type": "input_text", "text": "One"},
            {"type": "input_text", "text": "Two"},
        ]

    assert extract_skill_evidence("codex", changed(source, 4, retag_selection)).status == "unsupported"


def test_native_user_inventory_and_tagged_selection_require_exact_user_path():
    positive = extract_skill_evidence("codex", user_rows(selected=True))
    negative = extract_skill_evidence("codex", user_rows())
    assert positive.status == negative.status == "supported"
    assert [(fact.kind, fact.scope) for fact in positive.evidence] == [
        ("available", "user"),
        ("invoked", "user"),
    ]
    assert [(fact.kind, fact.scope) for fact in negative.evidence] == [("available", "user")]
    assert positive.evidence[0].location_sha256 == positive.evidence[1].location_sha256
    assert kind_recorded("codex", "invoked", "user")
    # A native parent-workspace skill cannot match the verified home file.
    candidate = {
        "component_id": "listing",
        "component_version_id": "version",
        "local_name": ALIAS,
        "scope": "user",
        "location_sha256": hashlib.sha256(
            f"/private/tmp/actual-home/.agents/skills/{ALIAS}/SKILL.md".encode()
        ).hexdigest(),
        "identity_status": "resolved",
        "verification_status": "verified",
    }
    result = match_skill_evidence(positive.evidence, {"v2": [candidate]}, {1: "v2", 3: "v2"})
    assert result.rows == () and result.unmatched_count == 2
    wrong_path = changed(
        user_rows(selected=True),
        3,
        lambda r: r["payload"]["content"][0].update(
            text=r["payload"]["content"][0]["text"].replace("/inert-codex-home/", "/another-home/")
        ),
    )
    assert extract_skill_evidence("codex", wrong_path).status == "unsupported"


def test_pasted_expansion_and_manual_file_read_do_not_select_a_skill():
    pasted = changed(
        rows(),
        2,
        lambda record: record["payload"]["content"][0].update(text="<skill><name>matrix-probe-skill</name></skill>"),
    )
    assert [e.kind for e in extract_skill_evidence("codex", pasted).evidence] == ["available"]
    manual = changed(
        rows(),
        2,
        lambda record: record["payload"]["content"][0].update(text="cat .agents/skills/matrix-probe-skill/SKILL.md"),
    )
    assert [e.kind for e in extract_skill_evidence("codex", manual).evidence] == ["available"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["payload"].pop("internal_chat_message_metadata_passthrough"),
        lambda r: r["payload"]["internal_chat_message_metadata_passthrough"].update(content_item_kinds=["user.text"]),
        lambda r: r["payload"]["internal_chat_message_metadata_passthrough"].update(turn_id="other-turn"),
        lambda r: r["payload"]["internal_chat_message_metadata_passthrough"].update(
            content_item_kinds=["skills.selected_skill_instructions", "user.text"]
        ),
        lambda r: r["payload"]["content"][0].update(
            text=r["payload"]["content"][0]["text"].replace("/private/tmp/observal-codex-proof", "/private/tmp/other")
        ),
        lambda r: r["payload"]["content"][0].update(
            text=r["payload"]["content"][0]["text"].replace("<name>matrix-probe-skill</name>", "<name>other</name>")
        ),
        lambda r: r["payload"]["content"][0].update(
            text=r["payload"]["content"][0]["text"].replace("<skill>", "<skill>\n<skill>")
        ),
        lambda r: r.update(timestamp="invalid"),
    ],
)
def test_bad_or_spoofed_selected_source_withholds_whole_session(mutation):
    assert extract_skill_evidence("codex", changed(selected_rows(), 3, mutation)).status == "unsupported"


def test_two_distinct_selected_skills_in_one_turn_are_separate_facts():
    other = "matrix-other-skill"
    items = selected_rows()
    listing = json.loads(items[1]["raw_line"])
    listing["payload"]["content"][0]["text"] += f"- {other}: Inert (file: r0/{other}/SKILL.md)\n"
    items[1]["raw_line"] = json.dumps(listing)
    first = json.loads(items[3]["raw_line"])
    first["payload"]["id"] = "msg-second-selected"
    first["payload"]["content"][0]["text"] = first["payload"]["content"][0]["text"].replace(ALIAS, other)
    items.append({"is_source_record": 1, "line_offset": 5, "raw_line": json.dumps(first), "line_hash": "second-hash"})
    result = extract_skill_evidence("codex", items)
    assert result.status == "supported"
    assert [(e.kind, e.alias) for e in result.evidence] == [
        ("available", other),
        ("available", ALIAS),
        ("invoked", ALIAS),
        ("invoked", other),
    ]


def test_duplicate_or_reordered_selected_source_is_unsupported():
    original = selected_rows()
    duplicated = [*original, dict(original[3], line_offset=5)]
    assert extract_skill_evidence("codex", duplicated).status == "unsupported"
    assert extract_skill_evidence("codex", [*original, original[3]]).status == "unsupported"
    before_prompt = [dict(original[3], line_offset=2), dict(original[2], line_offset=3), *original[:2], original[4]]
    assert extract_skill_evidence("codex", before_prompt).status == "unsupported"


def test_missing_native_user_kind_is_not_a_zero_use_session():
    assert (
        extract_skill_evidence(
            "codex", changed(rows(), 2, lambda r: r["payload"].pop("internal_chat_message_metadata_passthrough"))
        ).status
        == "unsupported"
    )


def test_malformed_missing_or_unverified_version_is_not_zero():
    assert extract_skill_evidence("codex", rows()[1:]).status == "unsupported"
    assert (
        extract_skill_evidence(
            "codex", changed(rows(), 0, lambda record: record["payload"].update(cli_version="0.164.0"))
        ).status
        == "unsupported"
    )
    assert (
        extract_skill_evidence(
            "codex", changed(rows(), 1, lambda record: record["payload"]["content"][0].update(text="other"))
        ).status
        == "unsupported"
    )
    assert extract_skill_evidence("codex", rows() + rows()[:1]).status == "unsupported"
    assert (
        extract_skill_evidence(
            "codex", [dict(row, raw_line="{") if i == 2 else row for i, row in enumerate(rows())]
        ).status
        == "unsupported"
    )
    assert extract_skill_evidence("codex", selected_rows()[:3] + selected_rows()[4:]).status == "unsupported"


def test_cannot_spoof_inventory_with_user_or_assistant_message():
    changed_role = changed(rows(), 1, lambda record: record["payload"].update(role="user"))
    assert extract_skill_evidence("codex", changed_role).status == "unsupported"
    changed_type = changed(rows(), 1, lambda record: record.update(type="event_msg"))
    assert extract_skill_evidence("codex", changed_type).status == "unsupported"


def test_other_roots_or_ambiguous_aliases_never_match():
    different = changed(
        rows(),
        1,
        lambda record: record["payload"]["content"][0].update(
            text=record["payload"]["content"][0]["text"].replace(
                "/observal-codex-proof/.agents/skills", "/other/unrecognized/skills"
            )
        ),
    )
    assert extract_skill_evidence("codex", different).status == "unsupported"
    for extra_line in (
        "- matrix-probe-skill: Another scope (file: r1/matrix-probe-skill/SKILL.md)\n",
        "- matrix-probe-skill: Duplicate (file: r0/matrix-probe-skill/SKILL.md)\n",
    ):

        def duplicate(record, line=extra_line):
            text = record["payload"]["content"][0]["text"]
            record["payload"]["content"][0]["text"] = (
                text.replace("### Available skills\n", "- `r1` = `/tmp/user/.agents/skills`\n### Available skills\n")
                + line
            )

        assert extract_skill_evidence("codex", changed(rows(), 1, duplicate)).status == "unsupported"
    # Parent skills are real native listings, but cannot establish this project's availability.
    for replaced in (
        "- `r0` = `/tmp/another/unrecognized/skills`\n",  # reused root id
        "- `r9` = `/tmp/project/../different/.agents/skills`\n",  # noncanonical root
        "- other: malformed project entry\n",  # unparsed inventory line
    ):
        malformed = changed(
            rows(),
            1,
            lambda record, line=replaced: record["payload"]["content"][0].update(
                text=record["payload"]["content"][0]["text"].replace(
                    "### Available skills\n", line + "### Available skills\n"
                )
                if line.startswith("- `r")
                else record["payload"]["content"][0]["text"] + line
            ),
        )
        assert extract_skill_evidence("codex", malformed).status == "unsupported"


def test_selected_skill_matches_only_contemporaneous_verified_location():
    facts = extract_skill_evidence("codex", selected_rows()).evidence
    assert len(facts) == 2
    selected = facts[1]
    verified = {
        "component_id": "controlled-component",
        "component_version_id": "controlled-version",
        "local_name": ALIAS,
        "scope": "project",
        "location_sha256": selected.location_sha256,
        "identity_status": "resolved",
        "verification_status": "verified",
    }
    result = match_skill_evidence((selected,), {"same-layer": [verified]}, {3: "same-layer"})
    assert result.unmatched_count == 0
    assert len(result.rows) == 1
    assert (result.rows[0]["evidence_kind"], result.rows[0]["attribution_method"]) == (
        "skill_invoked",
        "verified_skill_install",
    )
    assert "path" not in result.rows[0]
    for changed_candidate in (
        verified | {"verification_status": "drifted"},
        verified | {"verification_status": "unverified"},
        verified | {"scope": "user"},
        verified | {"location_sha256": "0" * 64},
    ):
        unmatched = match_skill_evidence((selected,), {"same-layer": [changed_candidate]}, {3: "same-layer"})
        assert unmatched.rows == () and unmatched.unmatched_count == 1
    missing = match_skill_evidence((selected,), {"other-layer": [verified]}, {3: "same-layer"})
    assert missing.rows == () and missing.unmatched_count == 1
    collision = match_skill_evidence(
        (selected,), {"same-layer": [verified, verified | {"component_id": "other"}]}, {3: "same-layer"}
    )
    assert collision.rows == () and collision.collision_count == 1


def test_verified_project_file_location_and_shadow_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "workspace"
    file = project / ".agents" / "skills" / ALIAS / "SKILL.md"
    file.parent.mkdir(parents=True)
    file.write_text("---\nname: matrix-probe-skill\n---\nSafe fixture\n")
    expected = f"sha256-{hashlib.sha256(file.read_bytes()).hexdigest()}"
    active = f"project:.agents/skills/{ALIAS}/SKILL.md"
    assert CodexAdapter().skill_manifest_path("project", ALIAS) == active
    assert CodexAdapter().skill_manifest_path("user", ALIAS) == f"user:.agents/skills/{ALIAS}/SKILL.md"
    assert _skill_status("codex", "project", str(project), ALIAS, expected, {active: expected}) == "verified"
    assert (
        skill_location_sha256("codex", "project", str(project), ALIAS)
        == hashlib.sha256(str(file.resolve()).encode()).hexdigest()
    )
    pin = {
        "harnesses": {
            "codex": {
                "agents": [],
                "standalone": [
                    {
                        "type": "skill",
                        "id": "5d0c5ffb-c875-4e1e-9948-e53b4c0eeaa4",
                        "local_name": ALIAS,
                        "scope": "project",
                        "directory": str(project),
                        "skill_integrity": expected,
                    }
                ],
            }
        }
    }
    fingerprint = skill_verification_entry("codex", pin, str(project))
    assert fingerprint["path"] == "observal:skill-verification"
    original = build_layer_manifest("codex", str(project), registry_data=pin)
    assert {row["path"]: row["hash"] for row in original}[active] == expected
    assert fingerprint in original
    file.write_text("modified")
    modified = build_layer_manifest("codex", str(project), registry_data=pin)
    new_hash = {row["path"]: row["hash"] for row in modified}[active]
    assert new_hash != expected
    assert _skill_status("codex", "project", str(project), ALIAS, expected, {active: new_hash}) == "drifted"
    file.write_text("---\nname: matrix-probe-skill\n---\nSafe fixture\n")
    shadow = Path.home() / ".codex" / "skills" / ALIAS / "SKILL.md"
    shadow.parent.mkdir(parents=True)
    shadow.write_text("shadow")
    assert _skill_status("codex", "project", str(project), ALIAS, expected, {active: expected}) == "unverified"


def test_verified_user_file_pin_and_project_home_shadow_fail_closed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = home / "workspace"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    user_file = home / ".agents" / "skills" / ALIAS / "SKILL.md"
    user_file.parent.mkdir(parents=True)
    user_file.write_text("---\nname: matrix-probe-skill\n---\nInert user skill\n")
    expected = f"sha256-{hashlib.sha256(user_file.read_bytes()).hexdigest()}"
    active = f"user:.agents/skills/{ALIAS}/SKILL.md"
    adapter = CodexAdapter()
    assert adapter.skill_manifest_path("user", ALIAS) == active
    assert (
        skill_location_sha256("codex", "user", str(project), ALIAS)
        == hashlib.sha256(str(user_file.resolve()).encode()).hexdigest()
    )
    pin = {
        "harnesses": {
            "codex": {
                "agents": [],
                "standalone": [
                    {
                        "type": "skill",
                        "id": "5d0c5ffb-c875-4e1e-9948-e53b4c0eeaa4",
                        "local_name": ALIAS,
                        "scope": "user",
                        "skill_integrity": expected,
                    }
                ],
            }
        }
    }
    original = build_layer_manifest("codex", str(project), registry_data=pin)
    hashes = {item["path"]: item["hash"] for item in original}
    assert hashes[active] == expected
    assert skill_verification_entry("codex", pin, str(project)) in original
    assert _skill_status("codex", "user", str(project), ALIAS, expected, hashes) == "verified"
    assert _skill_status("codex", "user", None, ALIAS, expected, hashes) == "unverified"

    project_file = project / ".agents" / "skills" / ALIAS / "SKILL.md"
    project_file.parent.mkdir(parents=True)
    project_file.write_bytes(user_file.read_bytes())
    assert _skill_status("codex", "user", str(project), ALIAS, expected, hashes) == "unverified"
    project_file.unlink()
    codex_shadow = home / ".codex" / "skills" / ALIAS / "SKILL.md"
    codex_shadow.parent.mkdir(parents=True)
    codex_shadow.write_text("another copy")
    assert _skill_status("codex", "user", str(project), ALIAS, expected, hashes) == "unverified"
    codex_shadow.unlink()
    user_file.write_text("modified")
    changed_hashes = {
        item["path"]: item["hash"] for item in build_layer_manifest("codex", str(project), registry_data=pin)
    }
    assert changed_hashes[active] != expected
    assert _skill_status("codex", "user", str(project), ALIAS, expected, changed_hashes) == "drifted"


def test_matching_requires_verified_identity_exact_location_and_snapshot():
    fact = extract_skill_evidence("codex", rows()).evidence[0]
    candidate = {
        "component_id": "component-id",
        "component_version_id": "version-id",
        "local_name": ALIAS,
        "scope": "project",
        "location_sha256": fact.location_sha256,
        "identity_status": "resolved",
        "verification_status": "verified",
    }
    assert match_skill_evidence((fact,), {"v2_layer": [candidate]}, {1: "v2_layer"}).rows[0]["evidence_kind"] == (
        "skill_available"
    )
    for replacement in (
        {"verification_status": "unverified"},
        {"identity_status": "unknown"},
        {"location_sha256": "0" * 64},
        {"scope": "user"},
    ):
        result = match_skill_evidence((fact,), {"v2_layer": [candidate | replacement]}, {1: "v2_layer"})
        assert result.rows == ()
        assert result.unmatched_count == 1
    other = candidate | {"component_id": "other"}
    result = match_skill_evidence((fact,), {"v2_layer": [candidate, other]}, {1: "v2_layer"})
    assert result.rows == () and result.collision_count == 1


@pytest.mark.asyncio
async def test_codex_only_cohort_reports_offered_and_measured_no_invocation(monkeypatch):
    async def warehouse(sql, _params):
        if sql == skill_queries._SKILL_SUMMARY:
            return [
                {
                    "supported_present_sessions": 1,
                    "projection_complete_sessions": 1,
                    "usage_measurable_sessions": 1,
                    "available_scope_present_sessions": 1,
                    "invoked_scope_present_sessions": 1,
                    "available_measurable_sessions": 1,
                    "invoked_measurable_sessions": 1,
                    "available_sessions": 1,
                    "loaded_sessions": 0,
                    "total_confirmed_loads": 0,
                    "total_load_attempts": 0,
                    "observed_sessions": 0,
                    "candidate_facts": 2,
                    "attributed_facts": 1,
                    "unmatched_facts": 1,
                }
            ]
        if sql == skill_queries._HARNESSES:
            return [{"harness": "codex", "sessions": 1}]
        return [{}]

    monkeypatch.setattr(skill_queries, "_rows", warehouse)
    monkeypatch.setattr(
        skill_queries,
        "presence_coverage",
        AsyncMock(
            return_value={
                "present_sessions": 1,
                "present_users": 1,
            }
        ),
    )
    monkeypatch.setattr(skill_queries, "presence_version_distribution", AsyncMock(return_value={}))
    now = datetime.now(UTC)
    listing = "11111111-1111-4111-8111-111111111111"
    result = await skill_queries.skill_activity_summary("default", listing, None, (now - timedelta(days=1), now))
    assert result["available_sessions"] == 1
    assert (result["loaded_sessions"], result["confirmed_loads"], result["load_attempts"]) == (None, None, None)
    assert (result["invoked_sessions"], result["invocations"]) == (0, 0)
    assert result["coverage"].usage_rate_denominator_sessions == 1
    assert result["coverage"].attribution_state == "no_observed_skill_use"
    assert result["coverage"].usage_rate == 0
    assert "availability_only_harness_sessions" not in result["coverage"].reasons
    assert any(x.startswith("loads_not_recorded") for x in result["coverage"].limitations)
    params = skill_queries._params("default", listing, None, (now - timedelta(days=1), now))
    assert "codex" in params["param_supported"] and "codex" in params["param_use_capable"]
    assert "codex" in params["param_scope_gated"]
    assert "claude-code" not in params["param_scope_gated"] and "pi" not in params["param_scope_gated"]
    assert "has({use_capable:Array(String)}, harness)" in skill_queries._SKILL_SUMMARY
    assert "AND scope_measurable) AS usage_measurable_sessions" in skill_queries._SKILL_SUMMARY

    from services.insights import component_report

    narrative = component_report.generate_skill_sections(result, result["coverage"].model_dump(mode="json"))
    assert "No observed selection, invocation or confirmed load" in narrative["summary"]
    assert "does not prove the skill was never used" in narrative["summary"]


@pytest.mark.asyncio
async def test_user_only_codex_skill_keeps_use_and_availability_unknown(monkeypatch):
    """A project inventory can coexist with a verified *user* pin; it proves no user-skill zero."""

    async def warehouse(sql, _params):
        if sql == skill_queries._SKILL_SUMMARY:
            return [
                {
                    "supported_present_sessions": 1,
                    "projection_complete_sessions": 1,
                    "scope_unmeasurable_sessions": 1,
                    "usage_measurable_sessions": 0,
                    "available_measurable_sessions": 0,
                    "invoked_measurable_sessions": 0,
                }
            ]
        if sql == skill_queries._HARNESSES:
            return [{"harness": "codex", "sessions": 1}]
        return [{}]

    monkeypatch.setattr(skill_queries, "_rows", warehouse)
    monkeypatch.setattr(skill_queries, "presence_coverage", AsyncMock(return_value={"present_sessions": 1}))
    monkeypatch.setattr(skill_queries, "presence_version_distribution", AsyncMock(return_value={}))
    now = datetime.now(UTC)
    summary = await skill_queries.skill_activity_summary(
        "default", "11111111-1111-4111-8111-111111111111", None, (now - timedelta(days=1), now)
    )
    assert (summary["available_sessions"], summary["invoked_sessions"], summary["invocations"]) == (
        None,
        None,
        None,
    )
    coverage = summary["coverage"]
    assert coverage.projection.projection_complete_sessions == 1
    assert coverage.usage_rate_denominator_sessions == 0 and coverage.usage_rate is None
    assert coverage.attribution_state == "attribution_not_possible"
    assert "skill_scope_or_offer_not_verified" in coverage.reasons
    assert "availability_only_harness_sessions" not in coverage.reasons
    assert not any("not_recorded_on_some_scopes" in reason for reason in coverage.reasons)


@pytest.mark.asyncio
async def test_verified_project_with_only_unrelated_user_offer_stays_unknown(monkeypatch):
    # Simulates the project-only pin from the native-root counterexample above:
    # a complete projection but no attributable offered fact for this listing.
    async def warehouse(sql, _params):
        if sql == skill_queries._SKILL_SUMMARY:
            return [
                {
                    "supported_present_sessions": 1,
                    "projection_complete_sessions": 1,
                    "scope_unmeasurable_sessions": 1,
                    "usage_measurable_sessions": 0,
                    "available_measurable_sessions": 0,
                    "invoked_measurable_sessions": 0,
                }
            ]
        if sql == skill_queries._HARNESSES:
            return [{"harness": "codex", "sessions": 1}]
        return [{}]

    monkeypatch.setattr(skill_queries, "_rows", warehouse)
    monkeypatch.setattr(skill_queries, "presence_coverage", AsyncMock(return_value={"present_sessions": 1}))
    monkeypatch.setattr(skill_queries, "presence_version_distribution", AsyncMock(return_value={}))
    now = datetime.now(UTC)
    summary = await skill_queries.skill_activity_summary(
        "default", "11111111-1111-4111-8111-111111111111", None, (now - timedelta(days=1), now)
    )
    coverage = summary["coverage"]
    assert coverage.projection.projection_complete_sessions == 1
    assert coverage.usage_rate_denominator_sessions == 0 and coverage.usage_rate is None
    assert coverage.attribution_state == "attribution_not_possible"
    assert (summary["available_sessions"], summary["invocations"]) == (None, None)
    assert "skill_scope_or_offer_not_verified" in coverage.reasons


@pytest.mark.asyncio
async def test_codex_mixed_project_and_user_scope_excludes_unmeasured_siblings(monkeypatch):
    async def warehouse(sql, _params):
        if sql == skill_queries._SKILL_SUMMARY:
            return [
                {
                    "supported_present_sessions": 3,
                    "projection_complete_sessions": 3,
                    "scope_unmeasurable_sessions": 2,
                    "usage_measurable_sessions": 1,
                    "available_scope_present_sessions": 1,
                    "invoked_scope_present_sessions": 1,
                    "available_measurable_sessions": 1,
                    "invoked_measurable_sessions": 1,
                    "available_sessions": 1,
                }
            ]
        if sql == skill_queries._HARNESSES:
            return [{"harness": "codex", "sessions": 3}]
        return [{}]

    monkeypatch.setattr(skill_queries, "_rows", warehouse)
    monkeypatch.setattr(skill_queries, "presence_coverage", AsyncMock(return_value={"present_sessions": 3}))
    monkeypatch.setattr(skill_queries, "presence_version_distribution", AsyncMock(return_value={}))
    now = datetime.now(UTC)
    summary = await skill_queries.skill_activity_summary(
        "default", "11111111-1111-4111-8111-111111111111", None, (now - timedelta(days=1), now)
    )
    assert (summary["available_sessions"], summary["invoked_sessions"], summary["invocations"]) == (1, 0, 0)
    coverage = summary["coverage"]
    assert coverage.usage_rate_denominator_sessions == 1 and coverage.usage_rate == 0
    assert coverage.projection.projection_complete_sessions == 3
    assert "skill_scope_or_offer_not_verified" in coverage.reasons
    assert "invoked_not_recorded_on_some_scopes" in coverage.reasons
    assert "available_not_recorded_on_some_scopes" in coverage.reasons


@pytest.mark.asyncio
async def test_pending_project_skill_does_not_make_complete_user_skill_a_zero(monkeypatch):
    async def warehouse(sql, _params):
        if sql == skill_queries._SKILL_SUMMARY:
            return [
                {
                    "supported_present_sessions": 2,
                    "projection_complete_sessions": 1,
                    "projection_pending_sessions": 1,
                    "scope_unmeasurable_sessions": 1,
                    "usage_measurable_sessions": 0,
                    "available_scope_present_sessions": 1,
                    "invoked_scope_present_sessions": 1,
                }
            ]
        if sql == skill_queries._HARNESSES:
            return [{"harness": "codex", "sessions": 2}]
        return [{}]

    monkeypatch.setattr(skill_queries, "_rows", warehouse)
    monkeypatch.setattr(skill_queries, "presence_coverage", AsyncMock(return_value={"present_sessions": 2}))
    monkeypatch.setattr(skill_queries, "presence_version_distribution", AsyncMock(return_value={}))
    now = datetime.now(UTC)
    summary = await skill_queries.skill_activity_summary(
        "default", "11111111-1111-4111-8111-111111111111", None, (now - timedelta(days=1), now)
    )
    assert (summary["available_sessions"], summary["invocations"]) == (None, None)
    coverage = summary["coverage"]
    assert coverage.attribution_state == "attribution_not_possible"
    assert coverage.usage_rate_denominator_sessions == 0
    assert "projection_pending_sessions" in coverage.reasons and "skill_scope_or_offer_not_verified" in coverage.reasons
    assert not any(item.startswith("invocations_not_recorded") for item in coverage.limitations)


@pytest.mark.asyncio
async def test_codex_scope_gates_each_session_row(monkeypatch):
    now = datetime.now(UTC)

    async def warehouse(_sql, _params):
        return [
            {
                "user_id": "u",
                "harness": "codex",
                "session_id": name,
                "last_event_time": now.isoformat(),
                "projection_state": "complete",
                "source_state": "complete",
                "scope_measurable": measurable,
                "available_facts": available,
                "invocations": 0,
            }
            for name, measurable, available in (
                ("project_offered", 1, 1),
                ("project_unoffered", 0, 0),
                ("user_unoffered", 0, 0),
                ("mixed", 0, 0),
            )
        ]

    monkeypatch.setattr(skill_queries, "_rows", warehouse)
    result, _ = await skill_queries.skill_activity_sessions(
        "default",
        "11111111-1111-4111-8111-111111111111",
        None,
        (now - timedelta(days=1), now),
        limit=10,
        cursor=None,
        cursor_scope=("default", "u", "skill", "id", "", "1"),
    )
    by_id = {session["session_id"]: session for session in result}
    assert (by_id["project_offered"]["available"], by_id["project_offered"]["invocations"]) == (True, 0)
    for name in ("project_unoffered", "user_unoffered", "mixed"):
        assert (by_id[name]["available"], by_id[name]["invocations"]) == (None, None)


@pytest.mark.asyncio
async def test_unprojected_codex_availability_is_unknown_not_zero(monkeypatch):
    async def warehouse(sql, _params):
        if sql == skill_queries._SKILL_SUMMARY:
            return [{"supported_present_sessions": 1, "projection_pending_sessions": 1}]
        if sql == skill_queries._HARNESSES:
            return [{"harness": "codex", "sessions": 1}]
        return [{}]

    monkeypatch.setattr(skill_queries, "_rows", warehouse)
    monkeypatch.setattr(skill_queries, "presence_coverage", AsyncMock(return_value={"present_sessions": 1}))
    monkeypatch.setattr(skill_queries, "presence_version_distribution", AsyncMock(return_value={}))
    now = datetime.now(UTC)
    summary = await skill_queries.skill_activity_summary(
        "default", "11111111-1111-4111-8111-111111111111", None, (now - timedelta(days=1), now)
    )
    assert all(
        summary[key] is None
        for key in (
            "available_sessions",
            "loaded_sessions",
            "confirmed_loads",
            "load_attempts",
            "invoked_sessions",
            "invocations",
        )
    )
    assert summary["coverage"].attribution_state == "attribution_not_possible"
