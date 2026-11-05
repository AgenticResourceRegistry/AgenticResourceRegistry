# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared JSON output contract coverage."""

from __future__ import annotations

import json

from observal_cli.render import list_envelope, output_json


def test_list_envelope_is_universal_and_empty_safe(capsys):
    assert list_envelope([]) == {"items": [], "total": 0, "page": 1, "page_size": 0}

    output_json([{"id": "one"}])

    assert json.loads(capsys.readouterr().out) == {
        "items": [{"id": "one"}],
        "total": 1,
        "page": 1,
        "page_size": 1,
    }


def test_raw_api_json_preserves_arrays(capsys):
    output_json([{"id": "one"}], raw=True)

    assert json.loads(capsys.readouterr().out) == [{"id": "one"}]


def test_list_object_gets_missing_pagination_fields(capsys):
    output_json({"items": [{"id": "one"}], "personalized": True})

    assert json.loads(capsys.readouterr().out) == {
        "items": [{"id": "one"}],
        "personalized": True,
        "total": 1,
        "page": 1,
        "page_size": 1,
    }


def test_complete_list_object_is_unchanged(capsys):
    output_json({"items": [], "total": 7, "page": 2, "page_size": 5})

    assert json.loads(capsys.readouterr().out) == {"items": [], "total": 7, "page": 2, "page_size": 5}
