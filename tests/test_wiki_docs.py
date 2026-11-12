# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Keep the shipped wiki navigable as source documentation changes."""

import re
from pathlib import Path

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


def test_harness_capability_tables_match_registry():
    from observal_shared.harness_registry import HARNESS_REGISTRY

    capabilities = ("mcp_servers", "skills", "hooks", "prompts")
    for name in ("adding-a-harness.md", "integrations/index.md"):
        rows = {}
        for line in (DOCS / name).read_text().splitlines():
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) == 6 and re.fullmatch(r"`[a-z][a-z-]*`", cells[1]):
                harness = cells[1].strip("`")
                assert harness not in rows, f"Duplicate harness in {name}: {harness}"
                assert all(cell in {"Yes", "No", "Registered only"} for cell in cells[2:]), line
                rows[harness] = {
                    capability
                    for capability, cell in zip(capabilities, cells[2:], strict=True)
                    if cell in {"Yes", "Registered only"}
                }
        assert set(rows) == set(HARNESS_REGISTRY), f"Missing or extra harness rows in {name}"
        for harness, spec in HARNESS_REGISTRY.items():
            assert rows[harness] == spec["capabilities"], f"Stale capabilities in {name}: {harness}"


def test_kiro_skill_paths_are_documented():
    from observal_shared.harness_registry import HARNESS_REGISTRY

    for name in ("adding-a-harness.md", "integrations/kiro.md"):
        content = (DOCS / name).read_text()
        for path in HARNESS_REGISTRY["kiro"]["skills"].values():
            assert f"`{path}`" in content, f"Stale Kiro skill path in {name}: {path}"


def test_harness_guide_repository_paths_exist():
    content = (DOCS / "adding-a-harness.md").read_text()
    paths = re.findall(r"`((?:observal_cli|observal-server|packages|tests)/[^`\s]+\.py)`", content)
    assert paths
    for path in paths:
        if "<" in path or "my_harness" in path:
            continue  # Template paths name files the contributor will create.
        assert (ROOT / path).is_file(), f"Harness guide references a missing source file: {path}"
