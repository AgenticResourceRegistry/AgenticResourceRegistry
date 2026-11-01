# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Smoke tests for the vendored ARD specification assets.

These guard the vendoring itself: the schema is loadable and has the
definitions the discovery code relies on, and the official conformance CLI
runs from its vendored location (its relative schema path must resolve).
Conformance of Observal's own endpoints is covered separately once they exist.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

VENDOR_DIR = Path(__file__).resolve().parents[1] / "observal-server" / "vendor" / "ard"
SCHEMA_PATH = VENDOR_DIR / "spec" / "schemas" / "ard-entry.schema.json"
CONFORMANCE_TOOL = VENDOR_DIR / "conformance" / "bin" / "conformance-test"

MINIMAL_MANIFEST = {
    "entries": [
        {
            "identifier": "urn:air:registry.example.com:skill:0f3c4d5e-6a7b-4c8d-9e0f-1a2b3c4d5e6f",
            "displayName": "Security Review",
            "type": "application/ai-skill+md",
            "url": "https://registry.example.com/api/v1/artifacts/skill/0f3c4d5e/1.2.0",
            "description": "Reviews code changes for security vulnerabilities.",
            "representativeQueries": [
                "review this pull request for security vulnerabilities",
                "look for authentication bugs in these changes",
            ],
        }
    ]
}


def test_vendored_files_present():
    for rel in (
        "spec/schemas/ard-entry.schema.json",
        "spec/schemas/ai-catalog.schema.json",
        "spec/schemas/ard.context.jsonld",
        "spec/schemas/ard.cddl",
        "spec/schemas/ard.openapi.yaml",
        "conformance/bin/conformance-test",
        "LICENSE",
        "VENDORED.md",
    ):
        assert (VENDOR_DIR / rel).is_file(), f"missing vendored file: {rel}"


def test_entry_schema_has_expected_definitions():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    defs = schema.get("$defs", {})
    assert {"ArdEntry", "ArdManifest", "EntryFields", "TrustManifest"} <= set(defs)
    assert set(defs["ArdEntry"]["required"]) == {"identifier", "displayName", "type"}
    assert defs["ArdManifest"]["required"] == ["entries"]


def test_vendored_pin_is_recorded():
    text = (VENDOR_DIR / "VENDORED.md").read_text(encoding="utf-8")
    assert "b76f235a8f461876ad4f1e77abd0eb0eb302b48d" in text
    assert "ards-project/ard-spec" in text


def _run_conformance(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CONFORMANCE_TOOL), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_conformance_tool_passes_minimal_manifest(tmp_path: Path):
    manifest = tmp_path / "ard.json"
    manifest.write_text(json.dumps(MINIMAL_MANIFEST), encoding="utf-8")
    result = _run_conformance("manifest", str(manifest))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONFORMANCE STATUS: PASS" in result.stdout
    # The relative schema path must resolve from the vendored location.
    assert "Schema file not found" not in result.stdout


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        (lambda e: e.pop("identifier"), "Missing required 'identifier'"),
        (lambda e: e.__setitem__("identifier", "not-a-urn"), "does not match"),
        (lambda e: e.__setitem__("data", {"inline": True}), "url"),
    ],
)
def test_conformance_tool_rejects_invalid_entries(tmp_path: Path, mutation, expected_fragment: str):
    manifest = json.loads(json.dumps(MINIMAL_MANIFEST))
    mutation(manifest["entries"][0])
    path = tmp_path / "ard.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = _run_conformance("manifest", str(path))
    assert result.returncode == 1, result.stdout
    assert "CONFORMANCE STATUS: FAIL" in result.stdout
    assert expected_fragment in result.stdout
