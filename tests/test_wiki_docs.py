# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Keep the shipped wiki navigable as source documentation changes."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
WEB_LIB = ROOT / "web/src/lib"


def _wiki_paths() -> set[str]:
    loader = (WEB_LIB / "docs-loader.ts").read_text()
    visible, imports = loader.split("const docModules", 1)
    paths = set(re.findall(r'^\s*"([\w/.-]+\.md)",?$', visible, re.MULTILINE))
    bundled = set(re.findall(r'^\s*"\.\./\.\./\.\./docs/([\w/.-]+\.md)",?', imports, re.MULTILINE))
    assert paths == bundled, "Wiki navigation and Vite's bundled document list must match"
    return paths


def _doc_exists(doc: Path, href: str) -> bool:
    """Resolve a docs link the way the docs site and the in-app wiki do."""
    path = href.split("#", 1)[0]
    if not path:
        return True
    if path.startswith("/"):
        route = path.strip("/")
        candidates = [f"{route}.md", f"{route}/index.md"] if route else ["index.md"]
        return any((DOCS / name).is_file() for name in candidates)
    target = (doc.parent / path).resolve()
    return (target / "index.md").is_file() if target.is_dir() else target.is_file()


def test_wiki_pages_exist_and_links_resolve():
    pages = _wiki_paths()
    assert pages
    for name in pages:
        doc = DOCS / name
        assert doc.is_file(), f"Wiki page is missing: {name}"
        for href in re.findall(r"\]\(([^)\s]+)\)", doc.read_text()):
            if href.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if re.search(r"\.(png|svg|jpe?g|gif|json|ya?ml|py|sh|toml|txt)$", href.split("#", 1)[0]):
                continue
            # Pages the wiki does not bundle open on the docs site, but the page must exist.
            assert _doc_exists(doc, href), f"Broken link in {name}: {href}"


def test_contextual_help_targets_bundled_wiki_pages():
    pages = _wiki_paths()
    for file in re.findall(r'file: "([\w/.-]+\.md)"', (WEB_LIB / "docs-map.ts").read_text()):
        assert file in pages, f"Help panel targets a missing wiki page: {file}"


def test_self_hosting_and_install_docs_match_current_sources():
    import tomllib

    compose = (ROOT / "docker/docker-compose.yml").read_text()
    postgres = re.search(r"image: postgres:(\d+)", compose)
    assert postgres and f"PostgreSQL {postgres.group(1)}" in (DOCS / "self-hosting/ports-and-volumes.md").read_text()

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    install = (DOCS / "getting-started/installation.md").read_text()
    for name in re.findall(r"observal-cli\[([^]]+)\]", install):
        assert name in pyproject["project"]["optional-dependencies"]


# Documentation compatibility contract, reviewed separately from registry flags.
# Antigravity's native activation hold is documented in its integration guide;
# update this set only alongside reviewed compatibility evidence and that guide.
REGISTERED_ONLY_HARNESSES = frozenset({"antigravity"})


def _assert_capability_table(content: str, name: str) -> None:
    from observal_shared.harness_registry import HARNESS_REGISTRY

    capabilities = ("mcp_servers", "skills", "hooks", "prompts")
    rows = {}
    for line in content.splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 6 and re.fullmatch(r"`[a-z][a-z-]*`", cells[1]):
            harness = cells[1].strip("`")
            assert harness not in rows, f"Duplicate harness in {name}: {harness}"
            rows[harness] = cells[2:]
    assert set(rows) == set(HARNESS_REGISTRY), f"Missing or extra harness rows in {name}"
    for harness, spec in HARNESS_REGISTRY.items():
        enabled_label = "Registered only" if harness in REGISTERED_ONLY_HARNESSES else "Yes"
        expected = [enabled_label if capability in spec["capabilities"] else "No" for capability in capabilities]
        assert rows[harness] == expected, f"Stale capabilities or compatibility labels in {name}: {harness}"


@pytest.mark.parametrize("name", ("adding-a-harness.md", "integrations/index.md"))
def test_harness_capability_tables_match_registry(name):
    _assert_capability_table((DOCS / name).read_text(), name)


@pytest.mark.parametrize("name", ("adding-a-harness.md", "integrations/index.md"))
@pytest.mark.parametrize(
    ("old", "new"),
    (
        ("`antigravity` | Registered only", "`antigravity` | Yes"),
        ("`claude-code` | Yes", "`claude-code` | Registered only"),
    ),
)
def test_capability_checks_reject_compatibility_label_drift(name, old, new):
    content = (DOCS / name).read_text()
    assert old in content
    with pytest.raises(AssertionError, match="compatibility labels"):
        _assert_capability_table(content.replace(old, new, 1), name)


def test_kiro_skill_paths_are_documented():
    from observal_shared.harness_registry import HARNESS_REGISTRY

    for name in ("adding-a-harness.md", "integrations/kiro.md"):
        content = (DOCS / name).read_text()
        for path in HARNESS_REGISTRY["kiro"]["skills"].values():
            assert f"`{path}`" in content, f"Stale Kiro skill path in {name}: {path}"


def _assert_harness_source_paths(content: str) -> None:
    paths = re.findall(r"`((?:observal_cli|observal-server|services|packages|tests)/[^`\s]+\.py)`", content)
    assert paths
    for path in paths:
        if "<" in path or "my_harness" in path:
            continue  # Template paths name files the contributor will create.
        base = ROOT / "observal-server" if path.startswith("services/") else ROOT
        assert (base / path).is_file(), f"Harness guide references a missing source file: {path}"


def test_harness_guide_repository_paths_exist():
    _assert_harness_source_paths((DOCS / "adding-a-harness.md").read_text())


def test_harness_source_checks_reject_missing_server_relative_paths():
    content = (DOCS / "adding-a-harness.md").read_text()
    old = "services/config/skill_builder.py"
    assert old in content
    with pytest.raises(AssertionError, match="missing source file"):
        _assert_harness_source_paths(content.replace(old, "services/config/nonexistent_builder.py"))


def test_agent_context_capability_table_matches_registry():
    from observal_shared.harness_registry import HARNESS_REGISTRY

    by_display_name = {spec["display_name"]: (harness, spec) for harness, spec in HARNESS_REGISTRY.items()}
    by_display_name["Codex CLI"] = by_display_name["Codex"]
    rows = {}
    for line in (ROOT / "AGENTS.md").read_text().splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 6 or cells[0] not in by_display_name:
            continue
        harness, spec = by_display_name[cells[0]]
        assert harness not in rows
        rows[harness] = cells
        assert set(cells[1].split(", ")) == spec["capabilities"]
        assert cells[2].split("`")[1] == spec["session_parser"]
        assert cells[4].startswith("yes") == spec["headless_run"]
        has_evidence = any(
            spec.get(key) for key in ("invocation_extractor", "skill_evidence_extractor", "hook_evidence_extractor")
        )
        assert cells[5].startswith("yes") == has_evidence
    assert set(rows) == set(HARNESS_REGISTRY)


def test_settings_documentation_uses_canonical_harness_ids():
    from observal_shared.harness_registry import HARNESS_REGISTRY

    content = (DOCS / "self-hosting/miscellaneous.md").read_text()
    identifiers = content.split("Valid identifiers:", 1)[1].split("\n\n", 1)[0]
    assert set(re.findall(r"`([^`]+)`", identifiers)) == set(HARNESS_REGISTRY)
    harness_sections = content.split("## Harness Allowlist", 1)[1].split("## Git Mirror Path", 1)[0]
    for value in re.findall(r"^\| `([^`]+)` \|", harness_sections, re.MULTILINE):
        assert set(value.split(",")) <= set(HARNESS_REGISTRY), value


def test_harness_guide_covers_mcp_extractor_registration():
    content = (DOCS / "adding-a-harness.md").read_text().split("### MCP invocation evidence", 1)[1]
    section = content.split("### Skill and hook evidence", 1)[0]
    for reference in (
        "invocation_extractor",
        "_EXTRACTORS",
        "services/session_parsers/invocations.py",
        "services/session_parsers/invocation_types.py",
        "services/component_activity/matcher.py",
        "tests/test_component_activity_invocations.py",
        "tests/test_component_activity_pi_invocations.py",
    ):
        assert reference in section
