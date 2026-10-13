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


def test_wiki_pages_exist_and_link_only_to_bundled_pages():
    pages = _wiki_paths()
    assert pages
    for name in pages:
        doc = DOCS / name
        assert doc.is_file(), f"Wiki page is missing: {name}"
        for href in re.findall(r"\]\(([^)]+)\)", doc.read_text()):
            if href.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = href.split("#", 1)[0]
            target = (doc.parent / path).resolve()
            if target.is_dir():
                target /= "README.md"
            assert target.is_file(), f"Broken link in {name}: {href}"
            if target.is_relative_to(DOCS):
                assert str(target.relative_to(DOCS)) in pages, f"Unbundled link in {name}: {href}"


def test_contextual_help_targets_bundled_wiki_pages():
    pages = _wiki_paths()
    for file in re.findall(r'file: "([\w/.-]+\.md)"', (WEB_LIB / "docs-map.ts").read_text()):
        assert file in pages, f"Help panel targets a missing wiki page: {file}"


def test_quickstart_and_hook_reference_match_current_sources():
    import tomllib

    compose = (ROOT / "docker/docker-compose.yml").read_text()
    postgres = re.search(r"image: postgres:(\d+)", compose)
    assert postgres and f"PostgreSQL {postgres.group(1)}" in (DOCS / "getting-started/quickstart.md").read_text()

    hook_spec = (ROOT / "observal_cli/harness_specs/claude_code_hooks_spec.py").read_text()
    version = re.search(r'^HOOKS_SPEC_VERSION = "(\d+)"', hook_spec, re.MULTILINE)
    assert version and f'HOOKS_SPEC_VERSION = "{version.group(1)}"' in (DOCS / "reference/hooks-spec.md").read_text()

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    install = (DOCS / "getting-started/installation.md").read_text()
    for name in re.findall(r"observal-cli\[([^]]+)\]", install):
        assert name in pyproject["project"]["optional-dependencies"]
