# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Rare query words outweigh common ones in discovery ranking."""

from __future__ import annotations

from types import SimpleNamespace

from services.discovery.search import rank_entries


def _entry(name: str, description: str) -> SimpleNamespace:
    slug = name.lower().replace(" ", "-")
    return SimpleNamespace(
        display_name=name,
        native_ref=f"acme/{slug}@1.0.0",
        description=description,
        representative_queries=[],
        capabilities=[],
        tags=[],
        lifecycle_status=None,
        raw_entry={},
        ard_identifier=f"urn:air:acme.example:skill:{slug}",
    )


def _common_word_entries() -> list[SimpleNamespace]:
    return [_entry(f"Tool {c}", "deploy a service") for c in "bcde"] + [_entry("Tool a", "deploy a service")]


def test_rare_word_outranks_common_word():
    # "deploy" is in five entries, "zebra" in one. Each entry matches exactly one query word, so
    # an unweighted score ties them and the name sort would put "Tool a" first.
    entries = [*_common_word_entries(), _entry("Zed", "zebra stripes")]
    ranked = rank_entries("deploy zebra", entries)
    assert ranked[0].entry.display_name == "Zed"
    assert ranked[0].score > ranked[1].score


def test_word_nobody_matches_does_not_reorder_results():
    entries = [*_common_word_entries(), _entry("Zed", "zebra stripes")]
    with_noise = [r.entry.display_name for r in rank_entries("deploy zebra qwertyuiop", entries)]
    without = [r.entry.display_name for r in rank_entries("deploy zebra", entries)]
    assert with_noise == without


def test_exact_name_with_every_word_still_scores_100():
    entries = [_entry("Release Notes", "Writes release notes."), _entry("Other", "Something else.")]
    ranked = rank_entries("release notes", entries)
    assert ranked[0].entry.display_name == "Release Notes"
    assert ranked[0].score == 100
