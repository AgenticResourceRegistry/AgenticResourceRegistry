# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Recheck saved drafts against direct submission requirements before review."""

from fastapi import HTTPException
from pydantic import ValidationError


def require_submittable_draft(schema, listing) -> None:
    # Draft persistence deliberately permits incomplete input. Revalidate before review.
    _require_submittable(schema, listing)


def require_submittable_version(schema, listing, version) -> None:
    # The version stores the submitted configuration while the listing owns its
    # namespace, name and owner. Check their effective combination, not the old
    # listing payload that the version is replacing.
    payload = {
        field: getattr(version, field, getattr(listing, field, None))
        for field in schema.model_fields
        if hasattr(version, field) or hasattr(listing, field)
    }
    payload["name"] = listing.name
    _require_submittable(schema, payload)


def _require_submittable(schema, payload) -> None:
    try:
        schema.model_validate(payload, from_attributes=True)
    except ValidationError as exc:
        first = exc.errors(include_input=False, include_context=False)[0]
        field = ".".join(str(part) for part in first["loc"])
        message = str(first["msg"]).removeprefix("Value error, ")
        raise HTTPException(status_code=400, detail=f"{field}: {message}" if field else message) from exc
