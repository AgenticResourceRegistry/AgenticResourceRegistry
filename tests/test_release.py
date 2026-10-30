# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest
import tools.release as release
from tools.release import (
    Change,
    Commit,
    Contributor,
    ReleaseError,
    all_contributors,
    bump_version,
    choose_release,
    discover_changes,
    latest_tag,
    pr_body,
    prepend_changelog,
    render_changelog_section,
    render_release_notes,
    resolve_release_push,
    set_version,
    validate_version_channel,
    write_manifest,
)

ROOT = Path(__file__).resolve().parent.parent


def _change(**overrides):
    values = {
        "commits": ["a" * 40],
        "title": "feat(cli): add safe releases",
        "author_name": "Hari",
        "author_email": "hari@example.com",
        "pr": 42,
        "url": "https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/pull/42",
        "category": "Features",
    }
    values.update(overrides)
    return Change(**values)


def test_bump_version():
    assert bump_version("1.10.7", "patch") == "1.10.8"
    assert bump_version("1.10.7", "feature") == "1.11.0"
    assert bump_version("1.10.7", "major") == "2.0.0"


def test_release_picker_includes_entire_branch(monkeypatch):
    questionary = ModuleType("questionary")

    class Choice:
        def __init__(self, title, value, checked=False):
            self.title = title
            self.value = value
            self.checked = checked

    class Prompt:
        def __init__(self, answer):
            self.answer = answer

        def ask(self):
            return self.answer

    def select(question, *, choices, default):
        return Prompt("stable")

    questionary.Choice = Choice
    questionary.select = select
    questionary.checkbox = lambda *args, **kwargs: Prompt([0])
    questionary.confirm = lambda *args, **kwargs: Prompt(False)
    monkeypatch.setitem(sys.modules, "questionary", questionary)

    included, version, channel = choose_release([_change()], "release/1.10", {"v1.10.7"})

    assert included == [_change()]
    assert (version, channel) == ("1.10.8", "stable")


def test_changelog_prepends_without_rewriting_history():
    history = (
        "<!-- custom header -->\n\n# Changelog\n\n"
        "All notable changes to this project will be documented in this file.\n\n"
        "## [1.10.7] - hand-edited history\n\nKeep this text exactly.\n"
    )
    section = "## [1.10.8] - 2026-07-28\n\n### Fixes\n\n- Fixed it"

    updated = prepend_changelog(history, section, "1.10.8")

    assert updated.endswith("## [1.10.7] - hand-edited history\n\nKeep this text exactly.\n")
    assert section in updated


def test_changelog_rejects_duplicate_version():
    history = "# Changelog\n\nAll notable changes to this project will be documented in this file.\n\n## [1.0.0]\n"

    with pytest.raises(ReleaseError, match="already contains"):
        prepend_changelog(history, "## [1.0.0]", "1.0.0")


def test_changelog_requires_introduction():
    with pytest.raises(ReleaseError, match="introduction was not found"):
        prepend_changelog("# Changelog\n", "## [1.0.0]", "1.0.0")


def test_set_version_updates_project_toml_and_top_level_json(tmp_path):
    toml_path = tmp_path / "pyproject.toml"
    toml_path.write_text('[tool.example]\nversion = "keep"\n\n[project]\nname = "demo"\nversion = "1.0.0"\n')
    json_path = tmp_path / "package.json"
    json_path.write_text('{\n  "name": "demo",\n  "version": "1.0.0",\n  "nested": {\n    "version": "keep"\n  }\n}\n')

    set_version(toml_path, "1.1.0")
    set_version(json_path, "1.1.0")

    assert '[tool.example]\nversion = "keep"' in toml_path.read_text()
    assert '[project]\nname = "demo"\nversion = "1.1.0"' in toml_path.read_text()
    assert '  "version": "1.1.0"' in json_path.read_text()
    assert '    "version": "keep"' in json_path.read_text()


def test_set_version_rejects_missing_version(tmp_path):
    path = tmp_path / "package.json"
    path.write_text('{\n  "name": "demo"\n}\n')

    with pytest.raises(ReleaseError, match="top-level version"):
        set_version(path, "1.1.0")


def test_latest_tag_chooses_highest_stable_even_when_detached(monkeypatch):
    monkeypatch.setattr(release, "run", lambda *args, **kwargs: "v1.10.7\nv1.11.0-rc.1\nv1.10.9\n")

    assert latest_tag() == "v1.10.9"


def test_release_discovery_skips_prior_release_metadata(monkeypatch):
    release_commit = Commit("a" * 40, "Maintainer", "m@example.com", "chore(release): v1.10.8", "")
    feature_commit = Commit("b" * 40, "Contributor", "c@example.com", "feat: next change", "")
    monkeypatch.setattr(release, "commit_log", lambda revision_range: [release_commit, feature_commit])
    monkeypatch.setattr(
        release,
        "gh_json",
        lambda repo, endpoint: (
            [{"number": 1, "merged_at": "2026-08-01", "base": {"ref": "main"}, "title": release_commit.title}]
            if release_commit.sha in endpoint
            else []
        ),
    )

    changes = discover_changes("AgenticResourceRegistry/AgenticResourceRegistry", "c" * 40, "upstream/main")

    assert [change.title for change in changes] == ["feat: next change"]


def test_gh_json_retries_transient_failures_then_fails_loudly(monkeypatch):
    calls = []

    def flaky(*args):
        calls.append(args)
        if len(calls) < 3:
            raise release.ReleaseError("i/o timeout")
        return '{"ok": true}'

    monkeypatch.setattr(release, "run", flaky)
    monkeypatch.setattr(release.time, "sleep", lambda _: None)
    assert release.gh_json("o/r", "x") == {"ok": True}

    monkeypatch.setattr(release, "run", lambda *a: (_ for _ in ()).throw(release.ReleaseError("down")))
    with pytest.raises(release.ReleaseError, match="down"):
        release.gh_json("o/r", "x")


def test_note_overrides_apply_and_reject_unknown_prs():
    a, b = Change(["a"], "a", "n", "e", pr=1), Change(["b"], "b", "n", "e", pr=2)
    a.include_in_notes = False
    release.apply_note_overrides([a, b], (1,), (2,), (1,))
    assert a.include_in_notes and a.highlight and not b.include_in_notes
    release.apply_note_overrides([a, b], titles={2: "New"}, categories={2: "Fixes"}, breaking=(2,))
    assert (b.title, b.category, b.breaking, b.include_in_notes) == ("New", "Fixes", True, True)
    with pytest.raises(release.ReleaseError, match="Unknown category"):
        release.apply_note_overrides([a, b], categories={1: "Nope"})
    with pytest.raises(release.ReleaseError, match="#9"):
        release.apply_note_overrides([a, b], (9,), (), ())
    with pytest.raises(release.ReleaseError, match="overlap"):
        release.apply_note_overrides([a, b], (1,), (1,), ())


def test_resolve_release_push_uses_exact_commit_on_release_branch(monkeypatch):
    normal = Commit("a" * 40, "A", "a@example.com", "fix: normal", "")
    merged = Commit("b" * 40, "B", "b@example.com", "chore(release): v1.10.8", "")
    monkeypatch.setattr(release, "commit_log", lambda revision_range: [normal, merged])
    monkeypatch.setattr(release, "run", lambda *args, **kwargs: merged.sha if args[1] == "log" else "")
    assert resolve_release_push("a" * 40, "f" * 40, "release/1.10") == merged.sha


def test_resolve_release_push_rejects_manifest_without_release_commit(monkeypatch):
    normal = Commit("a" * 40, "A", "a@example.com", "fix: normal", "")
    monkeypatch.setattr(release, "commit_log", lambda revision_range: [normal])
    monkeypatch.setattr(release, "run", lambda *args, **kwargs: normal.sha)

    with pytest.raises(ReleaseError, match="ambiguous or malformed"):
        resolve_release_push("a" * 40, "f" * 40, "release/1.10")


def test_release_pr_instructions_allow_linear_merges():
    body = pr_body("1.10.8", "v1.10.7", "a" * 40, [_change()], "preview")

    assert "Rebase-merge into `release/1.10`" in body
    assert "merge commit" not in body


def test_write_manifest_supports_no_pull_requests(tmp_path):
    path = tmp_path / ".release.toml"

    write_manifest(path, "1.1.0", "stable", "v1.0.0", "a" * 40, [_change(pr=None)])

    manifest = tomllib.loads(path.read_text())
    assert manifest["version"] == "1.1.0"
    assert manifest["source_sha"] == "a" * 40
    assert manifest["branch"] == "release/1.1"
    assert manifest["included_prs"] == []


def test_version_must_match_release_channel():
    validate_version_channel("1.1.0", "stable")
    validate_version_channel("1.1.0-rc.1", "rc")

    with pytest.raises(ReleaseError, match="does not match"):
        validate_version_channel("1.1.0-rc.1", "stable")
    with pytest.raises(ReleaseError, match="does not match"):
        validate_version_channel("1.1.0-beta.1", "rc")


def test_commit_authors_are_included_as_contributors():
    contributors = all_contributors(
        [_change(contributor=Contributor("owner", "owner"))],
        [Commit("a" * 40, "Coauthor", "123+coauthor@users.noreply.github.com", "feat: work", "feat: work")],
    )

    assert [contributor.label for contributor in contributors] == ["@coauthor", "@owner"]


def test_human_names_ending_in_bot_are_not_filtered():
    contributors = all_contributors(
        [
            _change(contributor=Contributor("Talbot", "talbot")),
            _change(contributor=Contributor("dependabot", "dependabot[bot]")),
        ],
        [],
    )

    assert [contributor.label for contributor in contributors] == ["@talbot"]


def test_release_notes_include_version_and_comparison_link():
    notes = render_release_notes(
        "1.10.8",
        "v1.10.7",
        "a" * 40,
        [_change()],
        [Contributor("Hari", "hari"), Contributor("New Person", "new-person", first_time=True)],
    )

    assert "v1.10.7...v1.10.8" in notes
    assert "add safe releases" in notes
    assert "docs/security/release-verification.md" in notes


def test_release_workflow_signs_and_verifies_tags_before_push():
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    tag_job = workflow[workflow.index("  tag:\n") : workflow.index("  pypi:\n")]
    install_gitsign = tag_job[
        tag_job.index("      - name: Install gitsign 0.17.1") : tag_job.index("      - name: Create or verify tag")
    ]
    verify_tag = tag_job[tag_job.index("          verify_tag() {") : tag_job.index("          if git rev-parse")]

    assert "id-token: write" in tag_job
    assert "chainguard-dev/actions/setup-gitsign@9d631658f55713e5f63ca0cc21ee168f81301fd9" in tag_job
    assert 'GITSIGN_VERSION: "0.17.1"' in install_gitsign
    assert "69213a8a0813a151e5a47d0060862952ff833a845d57309dff76f7ba6600abae" in install_gitsign
    assert "sha256sum -c" in install_gitsign
    assert "gitsign version" in install_gitsign
    assert 'GITSIGN_ENABLE_SIGSTORE_GO: "true"' in tag_job
    assert 'GITSIGN_REKOR_MODE: "online"' in tag_job
    assert 'GITSIGN_REKOR_VERSION: "1"' in tag_job
    assert "git tag -s" in tag_job
    assert "gitsign verify-tag" in verify_tag
    assert "for attempt in {1..5}" in verify_tag
    assert '"$attempt" -eq 5' in verify_tag
    assert "sleep 15" in verify_tag
    assert tag_job.index("gitsign verify-tag") < tag_job.index("git push origin")
    assert "certificate-oidc-issuer" in tag_job
    assert "predates signed-tag enforcement" not in tag_job
    assert "existing tags cannot be replaced" in tag_job
    assert "release.yml@$GITHUB_REF" in tag_job


def test_cli_binary_bundles_skill_tree_and_smokes_startup():
    import yaml

    from observal_cli.skill_installer import _SKILL_DIRS, _bundled_sources

    spec = (ROOT / "observal_cli/pyinstaller.spec").read_text()
    assert '(str(cli_dir / "skills"), "observal_cli/skills")' in spec
    sources = _bundled_sources()
    assert set(sources) == set(_SKILL_DIRS)
    assert set(sources) == {path.name for path in (ROOT / "observal_cli/skills").iterdir() if path.is_dir()}
    for source in sources.values():
        assert (source / "SKILL.md").is_file()
        assert list((source / "references").glob("*.md")), f"Missing bundled references in {source}"

    steps = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())["jobs"]["cli-binaries"]["steps"]
    smoke = next(step for step in steps if step.get("name") == "Smoke-test binary")
    assert "--version" in smoke["run"]
    assert "config path --output json" in smoke["run"]


def test_server_release_package_contains_no_generated_secrets_or_tls_overlay():
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    package_job = workflow[workflow.index("  server-package:\n") : workflow.index("  approve:\n")]

    assert "docker-compose.observability.yml" in package_job
    assert "docker-compose.tls.yml" not in package_job
    assert '"$STAGING/secrets"' not in package_job


def test_changelog_uses_only_selected_public_notes():
    section = render_changelog_section(
        "1.10.8",
        "2026-07-28",
        [_change(), _change(title="ci: internal work", category="Maintenance", include_in_notes=False)],
    )

    assert "add safe releases" in section
    assert "internal work" not in section


def test_release_workflows_gate_stable_integrations_and_sign_branch_identity():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    jobs = workflow["jobs"]
    assert workflow[True]["push"]["branches"] == ["release/**"]
    assert workflow["concurrency"]["group"] == "release"
    assert workflow["concurrency"]["queue"] == "max"
    assert jobs["promote"]["needs"] == ["preflight", "verify"]
    assert "outputs.channel == 'stable'" in jobs["promote"]["if"]
    assert "version_tags()" in jobs["promote"]["steps"][-1]["run"]
    assert "homebrew" not in jobs
    assert jobs["deploy"]["needs"] == ["preflight", "promote"]
    assert "needs.promote.outputs.promoted == 'true'" in jobs["deploy"]["if"]
    trigger = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())[True]
    assert set(trigger) == {"workflow_call"}
    deploy = (ROOT / ".github/workflows/deploy.yml").read_text()
    assert 'git checkout --detach "$RELEASE_TARGET"' in deploy
    assert "origin/main" not in deploy
    assert "releases/latest" in deploy
    assert "release.yml@$GITHUB_REF" in (ROOT / ".github/workflows/release.yml").read_text()


def test_release_ci_and_terraform_keep_branch_coverage():
    import yaml

    for name in ("ci", "codeql", "dependency-review", "terraform", "e2e-frontend"):
        workflow = yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())
        assert workflow[True]["pull_request"]["branches"] == ["main", "release/**"]
    terraform = yaml.safe_load((ROOT / ".github/workflows/terraform.yml").read_text())
    assert "merge_group" in terraform[True]
    assert terraform[True]["push"]["branches"] == ["main", "release/**"]
    text = (ROOT / ".github/workflows/terraform.yml").read_text()
    assert "terraform apply" not in text
    assert "terraform init -backend=false" in text
    assert "terraform validate" in text
    assert "scripts/check_terraform_consistency.py" in text


def test_release_ruleset_is_linear_reviewed_and_requires_release_policy():
    import json

    ruleset = json.loads((ROOT / ".github/release-ruleset.json").read_text())
    assert ruleset["conditions"]["ref_name"]["include"] == ["refs/heads/release/*"]
    assert ruleset["bypass_actors"] == []
    rules = {rule["type"]: rule.get("parameters", {}) for rule in ruleset["rules"]}
    assert {"deletion", "non_fast_forward", "required_linear_history"} <= rules.keys()
    assert rules["pull_request"]["required_approving_review_count"] == 1
    assert rules["pull_request"]["allowed_merge_methods"] == ["rebase"]
    assert "release-policy" in {check["context"] for check in rules["required_status_checks"]["required_status_checks"]}


def test_prerelease_aliases_do_not_move_backward():
    assert release.distribution_tag("1.1.0-rc.1", "rc", ["v1.2.0-rc.1"]) == "next-1.1"
    assert release.distribution_tag("1.1.1-beta.1", "beta", ["v1.2.0-beta.2"]) == "beta-1.1"
    assert release.distribution_tag("1.2.0-rc.2", "rc", ["v1.2.0-rc.1"]) == "next"


def test_publishing_jobs_recheck_tags_on_failed_job_reruns():
    import yaml

    jobs = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())["jobs"]
    for name in ("docker-merge", "promote"):
        runs = "\n".join(step.get("run", "") for step in jobs[name]["steps"])
        assert "distribution_tag" in runs and "version_tags()" in runs
        assert jobs[name]["steps"][0]["with"]["fetch-depth"] == 0
    runs = "\n".join(step.get("run", "") for step in jobs["release"]["steps"])
    assert 'if [ "$DRAFT" = true ]; then\n  gh release edit "$VERSION" --draft=false --latest=false' in runs


def test_next_dev_version_and_title():
    assert release.next_dev_version("1.14") == "1.15.0-dev.0"
    assert release.DEV_TITLE.fullmatch("chore: start 1.15 development")
