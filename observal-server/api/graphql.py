# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Strawberry GraphQL schema for live UI subscriptions."""

from collections.abc import AsyncGenerator

import jwt
import strawberry
import structlog
from starlette.requests import HTTPConnection

from observal_shared.migration.constants import DEFAULT_PROJECT_ID
from services import dynamic_settings as ds
from services.jwt_service import decode_access_token
from services.redis import subscribe

logger = structlog.get_logger(__name__)


@strawberry.type
class Query:
    @strawberry.field
    def health(self) -> str:
        return "ok"


@strawberry.type
class SessionEvent:
    session_id: str
    event_name: str


@strawberry.type
class ReviewEvent:
    listing_id: str
    action: str
    number: int | None = None
    state: str | None = None


@strawberry.type
class Subscription:
    @strawberry.subscription
    async def session_updated(self, session_id: str | None = None) -> AsyncGenerator[SessionEvent, None]:
        channel = f"sessions:{session_id}:updated" if session_id else "sessions:updated"
        async for data in subscribe(channel):
            sid = data.get("session_id", "")
            if not session_id or sid == session_id:
                yield SessionEvent(session_id=sid, event_name=data.get("event_name", ""))

    @strawberry.subscription
    async def review_updated(
        self, info: strawberry.Info, listing_id: str | None = None, review_id: str | None = None
    ) -> AsyncGenerator[ReviewEvent, None]:
        if review_id:
            import uuid

            from api.deps import _authenticate_via_jwt
            from database import async_session
            from models.review import Review
            from services.review.decisions import _own_work, _target
            from services.teamspace import can_review, review_scope

            try:
                rid = uuid.UUID(review_id)
            except (TypeError, ValueError):
                raise ValueError("Valid review ID required") from None
            # Browsers cannot put Authorization in WebSocket headers. graphql-ws
            # sends it in connection_init; Strawberry attaches that payload to
            # context *after* get_context_dep has run.
            params = info.context.get("connection_params") or {}
            authorization = params.get("authorization") if isinstance(params, dict) else None
            authorization = authorization or info.context["request"].headers.get("authorization")
            if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
                raise ValueError("Authentication required")
            token = authorization.removeprefix("Bearer ").strip()

            async def is_allowed() -> bool:
                async with async_session() as db:
                    user = await _authenticate_via_jwt(token, db)
                    review = await db.get(Review, rid)
                    if user is None or review is None:
                        return False
                    subject, version = await _target(db, review)
                    return can_review(subject, await review_scope(db, user)) or _own_work(subject, version, user.id)

            if not await is_allowed():
                raise ValueError("Review not found")
            channel = f"review:{rid}:updated"
        else:
            channel = "reviews:updated"
        async for data in subscribe(channel):
            if review_id and not await is_allowed():
                # Also stop delivering after revocation, expiry or loss of scope.
                return
            lid = data.get("listing_id", "")
            if listing_id and lid != listing_id:
                continue
            yield ReviewEvent(
                listing_id=lid,
                action=data.get("action", data.get("kind", "")),
                number=data.get("number"),
                state=data.get("state"),
            )


async def _resolve_user_context_from_request(request) -> dict:
    import uuid as _uuid

    from sqlalchemy import select

    from database import async_session
    from models.user import User

    default = {
        "user_id": None,
        "user_role": None,
        "trace_privacy": ds.get_sync_bool("security.trace_privacy"),
    }

    auth: str | None = None
    if request is not None:
        auth = request.headers.get("authorization")
    if not auth or not auth.startswith("Bearer "):
        return default
    token = auth.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except jwt.InvalidTokenError:
        return default

    sub = payload.get("sub")
    if not sub:
        return default

    try:
        uid = _uuid.UUID(sub)
    except ValueError:
        return default

    try:
        async with async_session() as session:
            role = await session.scalar(select(User.role).where(User.id == uid))
            if role is None:
                return default
            return {
                "user_id": str(uid),
                "user_role": role.value,
                "trace_privacy": ds.get_sync_bool("security.trace_privacy"),
            }
    except Exception:
        logger.debug("Failed to resolve user context for GraphQL", exc_info=True)
        return default


def get_context(
    user_id: str | None = None,
    user_role: str | None = None,
    trace_privacy: bool = False,
) -> dict:
    return {
        "project_id": DEFAULT_PROJECT_ID,
        "user_id": user_id,
        "user_role": user_role,
        "trace_privacy": trace_privacy,
    }


async def get_context_dep(request: HTTPConnection) -> dict:
    ctx = await _resolve_user_context_from_request(request)
    return get_context(user_id=ctx["user_id"], user_role=ctx["user_role"], trace_privacy=ctx["trace_privacy"])


schema = strawberry.Schema(query=Query, subscription=Subscription)
