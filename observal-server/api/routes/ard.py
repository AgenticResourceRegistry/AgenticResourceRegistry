# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agentic Resource Discovery endpoints.

Spec surface (v0.91, vendored under ``vendor/ard``):

    GET  /.well-known/ard.json          manifest
    GET  /.well-known/ai-catalog.json   predecessor path, same document
    POST /api/v1/ard/search             Search (mandatory)
    GET  /api/v1/ard/agents             List (optional, deterministic browse)
    POST /api/v1/ard/explore            Explore (optional): answers 501 until facets ship

Observal-specific:

    GET  /api/v1/ard/entries/{identifier}   one complete entry by identifier

Authentication is optional everywhere here. Anonymous callers see public,
approved entries only when ``deployment.public_registry_enabled`` is on;
authenticated callers see what the registry's visibility rules already grant
them.
"""

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from loguru import logger as optic
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import services.dynamic_settings as ds
from api.deps import get_db, get_registry_user, optional_current_user
from api.ratelimit import limiter
from api.sanitize import escape_like
from models.discovery_entry import DiscoveryEntry, DiscoveryLifecycle, DiscoverySourceKind
from models.user import User
from schemas.ard import ArdError, ArdExploreRequest, ArdSearchRequest
from services.discovery.forks import public_upstreams
from services.discovery.identity import normalize_media_type, normalize_urn
from services.discovery.projection import PUBLISHER_DOMAIN_SETTING, ProjectionContext
from services.discovery.search import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    InvalidSearchRequestError,
    SearchFilters,
    decode_page_token,
    encode_page_token,
    search_entries,
)
from services.discovery.serialize import entry_document, list_item, manifest, search_result_item
from services.discovery.visibility import visible_entries_predicate

_REQUEST_LOCATIONS = frozenset({"body", "query", "path", "header", "cookie"})


def _validation_message(exc: RequestValidationError) -> str:
    """Name the first invalid field without echoing the client's input back."""
    errors = exc.errors()
    first = errors[0] if errors else {}
    if first.get("type") == "json_invalid":
        return "Request body is not valid JSON"
    loc = tuple(first.get("loc", ()))
    if loc and loc[0] in _REQUEST_LOCATIONS:
        loc = loc[1:]  # strip only the location marker; "query" is also a search body field
    field = ".".join(str(part) for part in loc)
    message = first.get("msg", "invalid request")
    return f"{field}: {message}" if field else message


class _ArdRoute(APIRoute):
    """Answer request-validation failures with the ARD error envelope.

    FastAPI's default is a 422 ``{"detail": [...]}``. ARD Appendix B requires
    every bad request to be a 400 carrying ``errorCode`` and ``message``, so a
    malformed body or query parameter must not fall through to that default.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def ard_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                return _error(400, "INVALID_ARGUMENT", _validation_message(exc))

        return ard_handler


router = APIRouter(tags=["ard"], route_class=_ArdRoute)

_BAD_REQUEST: dict[int | str, dict[str, Any]] = {
    400: {"model": ArdError, "description": "Invalid request (ARD error envelope)"},
}


def document_ard_validation_errors(app: FastAPI) -> None:
    """Drop the 422 FastAPI documents for every route with inputs; ARD routes answer 400 instead.

    FastAPI adds that 422 unless a route declares 422, 4XX or default, and
    nothing declared later can remove it. 4XX and default would both be
    wrong here, because auth (401) and rate-limit (429) failures do not use
    the ARD envelope, so the schema is corrected once it has been generated.
    """
    ard_paths = {route.path_format for route in router.routes if isinstance(route, _ArdRoute)}
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        schema = generate()
        for path in ard_paths:
            for operation in schema.get("paths", {}).get(path, {}).values():
                operation.get("responses", {}).pop("422", None)
        return schema

    app.openapi = openapi


# Anonymous discovery follows the registry-wide public switch. Unlike the
# other registry reads, a private deployment answers anonymous ARD calls with
# an empty (still conformant) result rather than 401, and the manifest carries
# only the registry's own entry.
PUBLIC_SEARCH_SETTING = "deployment.public_registry_enabled"
MANIFEST_LIMIT = 5000
SEARCH_RATE_LIMIT = "60/minute"


# ── Helpers ──────────────────────────────────────────────────────────────


def _error(status: int, code: str, message: str) -> JSONResponse:
    """ARD Appendix B error envelope."""
    return JSONResponse(status_code=status, content={"errorCode": code, "message": message})


async def _context() -> ProjectionContext:
    return ProjectionContext.from_public_url(
        await ds.get("deployment.public_url", ""),
        pinned_domain=await ds.get(PUBLISHER_DOMAIN_SETTING, ""),
    )


async def _public_search_enabled() -> bool:
    return await ds.get_bool(PUBLIC_SEARCH_SETTING, False)


async def discovery_user(
    request: Request,
    current_user: User | None = Depends(optional_current_user),
) -> User | None:
    """Optional caller: anonymous stays None; signed-in callers get the registry's usual enforcement."""
    if current_user is None:
        return None
    return await get_registry_user(request, current_user)


def _search_source(ctx: ProjectionContext) -> str:
    return f"{ctx.artifact_base_url}/api/v1/ard"


# ── Well-known manifest ──────────────────────────────────────────────────


async def _manifest_response(db: AsyncSession) -> JSONResponse:
    ctx = await _context()
    entries: list[DiscoveryEntry] = []
    if await _public_search_enabled():
        # The manifest describes this publisher's own resources; imported remote
        # agents keep their own publisher and are found through search instead.
        stmt = (
            select(DiscoveryEntry)
            .where(visible_entries_predicate(None, lifecycles=(DiscoveryLifecycle.approved,)))
            .where(DiscoveryEntry.source_kind == DiscoverySourceKind.local)
            .order_by(DiscoveryEntry.display_name, DiscoveryEntry.ard_identifier)
            .limit(MANIFEST_LIMIT)
        )
        entries = list((await db.execute(stmt)).scalars().all())
    upstreams = await public_upstreams(db, entries)
    optic.debug("ard manifest served entries={}", len(entries))
    return JSONResponse(
        content=manifest(
            entries, publisher_domain=ctx.publisher_domain, base_url=ctx.artifact_base_url, upstreams=upstreams
        ),
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/.well-known/ard.json", include_in_schema=False)
async def well_known_ard(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    return await _manifest_response(db)


@router.get("/.well-known/ai-catalog.json", include_in_schema=False)
async def well_known_ai_catalog(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Predecessor path. Same document; consumers MAY consult it (spec §5.1)."""
    return await _manifest_response(db)


# ── Search ───────────────────────────────────────────────────────────────


@router.post("/api/v1/ard/search", responses=_BAD_REQUEST)
@limiter.limit(SEARCH_RATE_LIMIT)
async def ard_search(
    request: Request,
    body: ArdSearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(discovery_user),
) -> JSONResponse:
    """ARD Search (§5.3.2). ``score`` is relevance only; approval and trust are separate fields."""
    ctx = await _context()
    try:
        filters = SearchFilters.from_ard_filter(body.query.filter)
        page = await search_entries(
            db,
            text=body.query.text or "",
            filters=filters,
            user=current_user,
            page_size=body.page_size or DEFAULT_PAGE_SIZE,
            page_token=body.page_token,
            public_search_enabled=await _public_search_enabled(),
        )
    except InvalidSearchRequestError as exc:
        return _error(400, "INVALID_ARGUMENT", exc.message)

    harness = filters.harnesses[0] if len(filters.harnesses) == 1 else None
    source = _search_source(ctx)
    upstreams = await public_upstreams(db, [r.entry for r in page.results])
    content: dict[str, Any] = {
        "results": [
            search_result_item(
                r,
                source=source,
                harness=harness,
                forked_from=upstreams.get((r.entry.kind, r.entry.local_entity_id)),
            )
            for r in page.results
        ]
    }
    if page.next_page_token:
        content["pageToken"] = page.next_page_token
    # Federation: an omitted field means "auto" bounded by the upstream allowlist
    # (ADR 0001, Decision 5). No upstream registries can be configured yet, so
    # every mode resolves to this registry alone; referrals mode says so with an
    # explicit empty list rather than by omission.
    if body.federation == "referrals":
        content["referrals"] = []
    optic.debug(
        "ard search user={} results={} total={} federation={}",
        current_user.id if current_user else None,
        len(page.results),
        page.total,
        body.federation or "auto",
    )
    return JSONResponse(content=content, headers={"Cache-Control": "no-store"})


# ── Explore ──────────────────────────────────────────────────────────────


@router.post("/api/v1/ard/explore", responses=_BAD_REQUEST)
async def ard_explore(_body: ArdExploreRequest) -> JSONResponse:
    """ARD Explore is optional; a registry without it returns 501 (§5.3.3)."""
    return _error(501, "NOT_IMPLEMENTED", "Explore is not implemented by this registry yet.")


# ── List ─────────────────────────────────────────────────────────────────

# Operators tried longest-first so ">=" is not read as ">" followed by "=".
_LIST_OPERATORS = (">=", "<=", "=", ">", "<")
_FIELD_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
_LIST_ORDER_FIELDS = {
    "displayname": DiscoveryEntry.display_name,
    "name": DiscoveryEntry.display_name,
    "updatedat": DiscoveryEntry.updated_at_source,
    "updated_at": DiscoveryEntry.updated_at_source,
    "createdat": DiscoveryEntry.indexed_at,
    "created_at": DiscoveryEntry.indexed_at,
    "version": DiscoveryEntry.version,
}


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(_unquote(value).replace("Z", "+00:00"))
    except ValueError:
        raise InvalidSearchRequestError("timestamp filters must be ISO 8601 values") from None


def _split_clauses(expression: str) -> list[str]:
    """Split on a case-insensitive AND keyword outside quotes, in one linear pass."""
    clauses: list[str] = []
    current: list[str] = []
    quote: str | None = None
    i = 0
    n = len(expression)
    while i < n:
        ch = expression[i]
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            current.append(ch)
            i += 1
            continue
        # A bare AND keyword: preceded and followed by whitespace or an edge.
        if (
            expression[i : i + 3].upper() == "AND"
            and (i == 0 or expression[i - 1].isspace())
            and (i + 3 == n or expression[i + 3].isspace())
        ):
            clause = "".join(current).strip()
            if clause:
                clauses.append(clause)
            current = []
            i += 3
            continue
        current.append(ch)
        i += 1
    clause = "".join(current).strip()
    if clause:
        clauses.append(clause)
    return clauses


def _parse_clause(clause: str) -> tuple[str, str, str]:
    """Return (field, operator, value) for ``field op value`` with a linear scan."""
    field_end = 0
    while field_end < len(clause) and clause[field_end] in _FIELD_CHARS:
        field_end += 1
    field = clause[:field_end]
    rest = clause[field_end:].lstrip()
    if not field or not field[0].isalpha():
        raise InvalidSearchRequestError("filter clauses must start with a field name")
    for op in _LIST_OPERATORS:
        if rest.startswith(op):
            value = rest[len(op) :].strip()
            if not value:
                raise InvalidSearchRequestError(f"filter clause for {field} has no value")
            return field, op, value
    raise InvalidSearchRequestError(f"filter clause for {field} needs one of =, >, >=, <, <=")


def _apply_list_filter(stmt, expression: str | None):
    """Apply the List API's EBNF-like filter (Appendix A). Clauses are ANDed; values may be comma-ORed."""
    if not expression or not expression.strip():
        return stmt
    for clause in _split_clauses(expression):
        field_name, op, value = _parse_clause(clause)
        field = field_name.lower()
        raw_values = [_unquote(v) for v in value.split(",") if v.strip()]
        if not raw_values:
            raise InvalidSearchRequestError(f"filter clause for {field_name} has no value")
        if field == "type":
            stmt = stmt.where(DiscoveryEntry.media_type.in_([normalize_media_type(v) or v for v in raw_values]))
        elif field in ("publisherid", "publisher"):
            stmt = stmt.where(DiscoveryEntry.publisher_domain.in_([v.lower() for v in raw_values]))
        elif field == "displayname":
            stmt = stmt.where(
                or_(
                    *[
                        func.lower(DiscoveryEntry.display_name).like(f"%{escape_like(value.lower())}%", escape="\\")
                        for value in raw_values
                    ]
                )
            )
        elif field in ("obs:kind", "kind"):
            stmt = stmt.where(DiscoveryEntry.kind.in_(raw_values))
        elif field == "createdafter":
            stmt = stmt.where(DiscoveryEntry.indexed_at > _parse_timestamp(raw_values[0]))
        elif field == "updatedafter":
            stmt = stmt.where(DiscoveryEntry.updated_at_source > _parse_timestamp(raw_values[0]))
        else:
            raise InvalidSearchRequestError(f"unsupported filter field: {field_name}")
        if op not in ("=", ">", ">=") or (op != "=" and field not in ("createdafter", "updatedafter")):
            raise InvalidSearchRequestError(f"operator {op} is not valid for {field_name}")
    return stmt


def _apply_order(stmt, order_by: str | None):
    if not order_by or not order_by.strip():
        return stmt.order_by(DiscoveryEntry.display_name, DiscoveryEntry.ard_identifier)
    parts = order_by.strip().split()
    column = _LIST_ORDER_FIELDS.get(parts[0].lower())
    if column is None or len(parts) > 2 or (len(parts) == 2 and parts[1].upper() not in ("ASC", "DESC")):
        raise InvalidSearchRequestError(f"invalid orderBy: {order_by!r}")
    direction = column.desc() if len(parts) == 2 and parts[1].upper() == "DESC" else column.asc()
    return stmt.order_by(direction, DiscoveryEntry.ard_identifier)


@router.get("/api/v1/ard/agents", responses=_BAD_REQUEST)
async def ard_list(
    filter: str | None = Query(default=None, max_length=1000),  # spec parameter name
    order_by: str | None = Query(default=None, alias="orderBy", max_length=100),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=MAX_PAGE_SIZE),
    page_token: str | None = Query(default=None, alias="pageToken", max_length=512),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(discovery_user),
) -> JSONResponse:
    """ARD List (§5.3.4): deterministic, cacheable browsing with no relevance ranking."""
    if current_user is None and not await _public_search_enabled():
        return JSONResponse(content={"items": [], "total": 0}, headers={"Cache-Control": "no-store"})

    fingerprint = f"list:{filter or ''}|{order_by or ''}|{current_user.id if current_user else 'anon'}"
    try:
        offset = decode_page_token(page_token, fingerprint)
        base = select(DiscoveryEntry).where(visible_entries_predicate(current_user))
        base = _apply_list_filter(base, filter)
        stmt = _apply_order(base, order_by).offset(offset).limit(page_size + 1)
    except InvalidSearchRequestError as exc:
        return _error(400, "INVALID_ARGUMENT", exc.message)

    rows = list((await db.execute(stmt)).scalars().all())
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    has_more = len(rows) > page_size
    upstreams = await public_upstreams(db, rows[:page_size])
    items = [list_item(e, forked_from=upstreams.get((e.kind, e.local_entity_id))) for e in rows[:page_size]]
    content: dict[str, Any] = {"items": items, "total": int(total)}
    if has_more:
        content["pageToken"] = encode_page_token(offset + page_size, fingerprint)
    return JSONResponse(content=content, headers={"Cache-Control": "no-store"})


# ── Entry by identifier ──────────────────────────────────────────────────


@router.get("/api/v1/ard/entries/{identifier:path}")
async def ard_entry(
    identifier: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(discovery_user),
) -> JSONResponse:
    """Return one complete ARD entry. Observal-specific: the spec defines no fetch-by-id."""
    if current_user is None and not await _public_search_enabled():
        return _error(404, "NOT_FOUND", "Entry not found")
    urn = normalize_urn(identifier)
    lifecycles = tuple(DiscoveryLifecycle)  # archived is fine when asked for directly
    stmt = select(DiscoveryEntry).where(
        DiscoveryEntry.ard_identifier == urn, visible_entries_predicate(current_user, lifecycles=lifecycles)
    )
    entry = (await db.execute(stmt)).scalar_one_or_none()
    if entry is None:
        return _error(404, "NOT_FOUND", "Entry not found")
    upstreams = await public_upstreams(db, [entry])
    return JSONResponse(
        content=entry_document(entry, forked_from=upstreams.get((entry.kind, entry.local_entity_id))),
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router"]
