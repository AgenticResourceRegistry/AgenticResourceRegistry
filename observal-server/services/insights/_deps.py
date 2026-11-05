# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Dependency container — wired up by the host application at startup.

The insights package does NOT import from observal-server directly.
Instead, the main app calls configure() which injects these dependencies.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

# Models that answered while one report was generated. A set shared through the
# context, so concurrent subtasks of the same report (asyncio.gather) add to it.
_models_used: ContextVar[set[str] | None] = ContextVar("insights_models_used", default=None)


@contextmanager
def recording_models() -> Iterator[set[str]]:
    """Collect the model IDs that produced output during the block (``llm_model_used``)."""
    used: set[str] = set()
    token = _models_used.set(used)
    try:
        yield used
    finally:
        _models_used.reset(token)


def note_model_used(model: str) -> None:
    """Record that ``model`` returned output, if a report is being generated."""
    used = _models_used.get()
    if used is not None and model:
        used.add(model)


# These are set by configure() at application startup
settings: Any = None
query: Callable[..., Awaitable[Any]] | None = None  # ClickHouse query fn
call_model: Callable[..., Awaitable[dict]] | None = None  # LLM model call fn
db_session: Callable[..., Any] | None = None  # async_session factory

# Model classes (set by configure())
InsightSessionFacets: type | None = None
InsightSessionMeta: type | None = None
InsightMetaCache: type | None = None


def configure(
    *,
    settings: Any = None,
    query_fn=None,
    call_model_fn=None,
    db_session_factory=None,
    meta_model=None,
    facets_model=None,
    meta_cache_model=None,
):
    """Wire up dependencies from the host application.

    Must be called before any insight generation functions are used.
    """
    import services.insights._deps as _self

    _self.settings = settings
    _self.query = query_fn
    _self.call_model = call_model_fn
    _self.db_session = db_session_factory
    _self.InsightSessionMeta = meta_model
    _self.InsightSessionFacets = facets_model
    _self.InsightMetaCache = meta_cache_model


def get_settings():
    if settings is None:
        raise RuntimeError("observal_insights not configured. Call configure() first.")
    return settings


def get_query():
    if query is None:
        raise RuntimeError("observal_insights not configured. Call configure() first.")
    return query


def get_call_model():
    if call_model is None:
        raise RuntimeError("observal_insights not configured. Call configure() first.")
    return call_model


def get_db_session():
    if db_session is None:
        raise RuntimeError("observal_insights not configured. Call configure() first.")
    return db_session


def get_facets_model():
    if InsightSessionFacets is None:
        raise RuntimeError("observal_insights not configured. Call configure() first.")
    return InsightSessionFacets


def get_meta_model():
    if InsightSessionMeta is None:
        raise RuntimeError("observal_insights not configured. Call configure() first.")
    return InsightSessionMeta


def get_meta_cache_model():
    if InsightMetaCache is None:
        raise RuntimeError("observal_insights not configured. Call configure() first.")
    return InsightMetaCache
