# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Smoke tests for the OSS-Fuzz targets under fuzz/.

Guards against the targets rotting when a parser is renamed or a seed corpus
stops being useful. Requires Atheris, which the fuzz targets import at module
scope; install it with ``pip install atheris`` or run ``make test-fuzz``.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("atheris")
pytest.importorskip("hypothesis")

FUZZ_DIR = Path(__file__).resolve().parent.parent / "fuzz"

# Fuzz targets run as standalone scripts, so their own directory is the import root.
if str(FUZZ_DIR) not in sys.path:
    sys.path.insert(0, str(FUZZ_DIR))

# oss-fuzz/build.sh discovers targets with exactly this glob.
TARGETS = sorted(path.stem for path in FUZZ_DIR.glob("*_fuzzer.py"))
CORPUS_TARGETS = sorted(target for target in TARGETS if (FUZZ_DIR / "corpus" / target).is_dir())


def test_targets_are_discoverable():
    assert TARGETS, "no fuzz targets matched fuzz/*_fuzzer.py"


@pytest.mark.parametrize("target", TARGETS)
def test_target_exposes_an_entry_point(target):
    """Every target must be importable and expose the Atheris bootstrap."""
    module = importlib.import_module(target)
    assert callable(module.main)


@pytest.mark.parametrize("target", CORPUS_TARGETS)
def test_seed_corpus_is_within_the_input_bound(target):
    """Seeds larger than the target's cap are rejected before reaching any parser."""
    module = importlib.import_module(target)
    for seed in sorted((FUZZ_DIR / "corpus" / target).iterdir()):
        size = seed.stat().st_size
        assert 0 < size <= module._MAX_INPUT_BYTES, f"{seed.name} is {size} bytes"


@pytest.mark.parametrize("target", CORPUS_TARGETS)
def test_seed_corpus_runs_clean(target):
    """The committed corpus must not crash the target it seeds."""
    module = importlib.import_module(target)
    for seed in sorted((FUZZ_DIR / "corpus" / target).iterdir()):
        module.TestOneInput(seed.read_bytes())


def test_session_seeds_reach_every_parser():
    """The first byte of a session seed selects the harness.

    Anything that rewrites these files -- a licence header hook, an editor
    adding a BOM -- changes that byte and quietly points the whole corpus at a
    single parser, which costs most of the corpus' reach without failing a
    single test. Assert every registered parser still has a seed.
    """
    from observal_shared.harness_registry import HARNESS_REGISTRY

    session = importlib.import_module("_session")

    seed_dir = FUZZ_DIR / "corpus" / "session_jsonl_fuzzer"
    seeded = {session.HARNESSES[seed.read_bytes()[0] % len(session.HARNESSES)] for seed in seed_dir.iterdir()}

    reached = {HARNESS_REGISTRY[harness]["session_parser"] for harness in seeded}
    expected = {spec["session_parser"] for spec in HARNESS_REGISTRY.values() if spec["session_parser"]}
    assert reached == expected, f"no seed reaches {sorted(expected - reached)}"


@pytest.mark.parametrize(
    ("harness", "record"),
    [
        ("copilot", {"type": "user.message", "ts": []}),
        ("copilot-cli", {"event": {"ts": {"bad": "timestamp"}}}),
        ("claude-code", {"type": "user", "timestamp": "not-a-date"}),
        ("pi", {"type": "message", "timestamp": 123}),
        ("kiro", {"payload": {}, "timestamp": []}),
        ("antigravity", {"created_at": "1800-01-01T00:00:00Z"}),
    ],
)
def test_session_oracle_treats_bad_source_timestamps_as_parse_errors(monkeypatch, harness, record):
    session = importlib.import_module("_session")
    parse = session.parse_raw_events
    captured = []

    def capture(rows):
        captured.extend(rows)
        return parse(rows)

    monkeypatch.setattr(session, "parse_raw_events", capture)
    session.replay(harness, [json.dumps(record), "{}"])

    assert len(captured) == 2
    assert captured[0]["event_type"] == "_parse_error"
    assert captured[0]["is_source_record"] == 1
    assert captured[0]["rendered"] == 0
    assert captured[1]["event_type"] != "_parse_error"


@pytest.mark.parametrize("failure_stage", ["classify", "preview", "tool"])
def test_session_oracle_treats_bad_source_fields_as_parse_errors(monkeypatch, failure_stage):
    session = importlib.import_module("_session")
    captured = []
    monkeypatch.setattr(session, "parse_raw_events", lambda rows: captured.extend(rows) or [])

    def bad_value(*_args):
        if failure_stage == "tool":
            return ([], None)
        return []

    classify, preview, tool = session.get_classifier("claude-code")
    functions = {"classify": classify, "preview": preview, "tool": tool}
    functions[failure_stage] = bad_value
    monkeypatch.setattr(session, "get_classifier", lambda _harness: tuple(functions.values()))

    session.replay("claude-code", ['{"type":"user","message":{"role":"user","content":"hello"}}'])
    assert captured[0]["event_type"] == "_parse_error"
    assert captured[0]["rendered"] == 0


@pytest.mark.parametrize(
    ("harness", "record"),
    [
        ("claude-code", {"type": "user", "uuid": []}),
        ("claude-code", {"type": "assistant", "message": {"usage": {"input_tokens": 2147483648}}}),
        ("kiro", {"kind": "Prompt", "uuid": "valid-id", "parentUuid": {"bad": "uuid"}}),
    ],
)
def test_session_oracle_rejects_bad_uuid_and_usage(monkeypatch, harness, record):
    session = importlib.import_module("_session")
    captured = []
    monkeypatch.setattr(session, "parse_raw_events", lambda rows: captured.extend(rows) or [])
    session.replay(harness, [json.dumps(record)])
    assert captured[0]["event_type"] == "_parse_error"
    assert captured[0]["rendered"] == 0


@pytest.mark.parametrize("stage", ["classifier", "uuid", "usage", "read_parser"])
def test_session_oracle_does_not_hide_infrastructure_errors(monkeypatch, stage):
    session = importlib.import_module("_session")

    def broken(*_args):
        raise RuntimeError("infrastructure failure")

    if stage == "classifier":
        _, preview, tool = session.get_classifier("copilot")
        monkeypatch.setattr(session, "get_classifier", lambda _harness: (broken, preview, tool))
    elif stage in {"uuid", "usage"}:
        monkeypatch.setattr(session, "_extract_uuid" if stage == "uuid" else "_extract_usage_tokens", broken)
    else:
        monkeypatch.setattr(session, "parse_raw_events", broken)
    with pytest.raises(RuntimeError, match="infrastructure failure"):
        session.replay("copilot", ['{"ts":"2026-01-01T00:00:00Z"}'])


def test_structured_session_property_holds():
    """Run the polyglot Hypothesis target as an ordinary property test."""
    module = importlib.import_module("session_structure_fuzzer")
    module.test_session_pipeline_contract()
