# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Discovery search: portable prefilter in SQL, deterministic ranking in Python.

Why not rank in the database: the index is small (hundreds to low thousands
of entries per deployment), the ranking must behave identically under SQLite
in tests and PostgreSQL in production, and explainable ``matchedOn`` output
is far easier to produce in Python. The database does what it is good at —
visibility, structured filters, and a cheap LIKE prefilter that a trigram GIN
index accelerates on PostgreSQL — and hands back a bounded candidate set.

``score`` is relevance only (ADR 0001, Decision 8). Approval, trust and
compatibility are separate fields on the result.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, or_, select

from api.sanitize import escape_like
from api.search import keyword_tokens
from models.discovery_entry import DiscoveryEntry, DiscoveryKind, DiscoveryLifecycle
from services.discovery.identity import normalize_media_type
from services.discovery.visibility import DEFAULT_LIFECYCLES, visible_entries_predicate

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Bounded so a pathological query cannot pull the whole table, but wide enough
# that a broad query over a large deployment (low thousands of entries) still
# ranks every candidate instead of dropping older ones before ranking.
CANDIDATE_LIMIT = 5000
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 10

# Field weights: an exact hit on the name is worth the most, then the queries
# publishers (or we) wrote to describe the need, then structured tokens, then
# free-text description.
_FIELD_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("name", 1.0),
    ("queries", 0.85),
    ("capabilities", 0.7),
    ("description", 0.5),
)
_MIN_PARTIAL_LEN = 4
# Cap on hyphen-separated parts expanded per token or display name (also keeps SQLite under its expression depth).
_MAX_HYPHEN_PARTS = 8
_MAX_NAME_PARTS = 32  # display-name words considered for hyphen-joined runs
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
# A trailing "@1.0.0" or "@latest" on a qualified reference selects a version; it is not a search word.
# Only after a word character, so "@scope/pkg" and "user@example.com" survive; the version-shaped
# pattern stops at the first character that cannot be part of a version, so "pr@1.0,other" keeps "other".
_VERSION_SUFFIX_RE = re.compile(
    r"(?<=[A-Za-z0-9_-])@(?:v?\d+(?:\.\d[\w+-]*)*(?:[-+][\w.+-]*)?(?!\w|\.\w)|(?:latest|stable|next|beta|alpha|rc)(?![\w.@/-]))"
)
# Only a whole whitespace-delimited token shaped "namespace/slug" names a namespace.
_QUALIFIED_TOKEN_RE = re.compile(r"@?([a-z0-9][\w.-]*)/@?[a-z0-9][\w.-]*")


class InvalidSearchRequestError(ValueError):
    """Raised for malformed queries; callers map it to ARD's INVALID_ARGUMENT.

    ``message`` is always a string this module chose, never another exception's
    text, so routes can return it to the client as-is.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ── Filters ──────────────────────────────────────────────────────────────


@dataclass(slots=True)
class SearchFilters:
    kinds: list[DiscoveryKind] = field(default_factory=list)
    media_types: list[str] = field(default_factory=list)
    harnesses: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)
    forks_of: str | None = None
    lifecycles: tuple[DiscoveryLifecycle, ...] = DEFAULT_LIFECYCLES
    activatable_only: bool = False

    @classmethod
    def from_ard_filter(cls, raw: dict[str, Any] | None) -> SearchFilters:
        """Parse an ARD ``query.filter`` object. Unknown keys are rejected (spec permits 400)."""
        filters = cls()
        if not raw:
            return filters
        if not isinstance(raw, dict):
            raise InvalidSearchRequestError("filter must be an object")

        def values(key: str) -> list[str]:
            value = raw[key]
            items = value if isinstance(value, list | tuple) else [value]
            out = [str(v).strip() for v in items if v is not None and str(v).strip()]
            if not out:
                raise InvalidSearchRequestError(f"filter {key!r} has no values")
            return out

        for key in raw:
            if key == "type":
                # type and obs:kind stay separate predicates so a disjoint pair returns nothing
                # rather than the union, and unknown media types remain filterable.
                filters.media_types = [normalize_media_type(v) or v for v in values(key)]
            elif key == "tags":
                filters.tags = [v.lower() for v in values(key)]
            elif key == "capabilities":
                filters.capabilities = [v.lower() for v in values(key)]
            elif key == "publisher":
                filters.publishers = [v.lower() for v in values(key)]
            elif key == "version":
                filters.versions = values(key)
            elif key in ("obs:kind", "kind"):
                for v in values(key):
                    try:
                        filters.kinds.append(DiscoveryKind(v))
                    except ValueError:
                        raise InvalidSearchRequestError(f"unknown kind in filter: {v}") from None
            elif key in ("obs:supportedHarnesses", "obs:harness"):
                filters.harnesses = values(key)
            elif key == "obs:lifecycle":
                lifecycles: list[DiscoveryLifecycle] = []
                for v in values(key):
                    try:
                        lifecycles.append(DiscoveryLifecycle(v))
                    except ValueError:
                        raise InvalidSearchRequestError(f"unknown lifecycle in filter: {v}") from None
                filters.lifecycles = tuple(lifecycles)
            elif key == "obs:forksOf":
                refs = values(key)
                if len(refs) != 1 or len(refs[0]) > 255:
                    raise InvalidSearchRequestError("obs:forksOf requires one source identifier")
                filters.forks_of = refs[0]
            elif key == "obs:activatable":
                filters.activatable_only = str(values(key)[0]).lower() in ("true", "1", "yes")
            else:
                raise InvalidSearchRequestError(f"unsupported filter term: {key}")
        return filters


# ── Ranking ──────────────────────────────────────────────────────────────


def _words(text: str | None) -> list[str]:
    """Lower-cased words; ``_`` and ``-`` are the same separator so ``pr_verify`` finds ``pr-verify``."""
    return [w.replace("_", "-") for w in _WORD_RE.findall((text or "").lower())]


def _qualified(entry: DiscoveryEntry) -> tuple[str, str]:
    """``(namespace, slug)`` from the native ref ``namespace/slug@version``; either may be empty."""
    ref = (entry.native_ref or "").split("@", 1)[0]
    namespace, _, slug = ref.rpartition("/")
    return namespace, slug


def _query_namespaces(text: str) -> frozenset[str]:
    """Namespaces of the tokens that are real ``namespace/slug`` references."""
    stripped = _VERSION_SUFFIX_RE.sub(" ", text.lower())
    matches = (_QUALIFIED_TOKEN_RE.fullmatch(t) for t in stripped.split())
    return frozenset(m.group(1) for m in matches if m)


def _fields(entry: DiscoveryEntry, namespaces: frozenset[str] = frozenset()) -> dict[str, set[str]]:
    namespace, slug = _qualified(entry)
    display = _words(entry.display_name)
    name = set(display) | set(_words(slug.replace("-", " "))) | set(_words(slug))
    # Hyphen-joined runs of the display name, so "release-notes" matches "Release Notes Writer".
    # Runs start at every part (bounded run length and name length) so a trailing "Release Notes" is found too.
    parts = [p for w in display for p in w.split("-") if p][:_MAX_NAME_PARTS]
    name |= {
        "-".join(parts[i:j])
        for i in range(len(parts))
        for j in range(i + 2, min(i + _MAX_HYPHEN_PARTS, len(parts)) + 1)
    }
    capabilities = set(w for c in [*(entry.capabilities or []), *(entry.tags or [])] for w in _words(c))
    if namespace.lower() in namespaces:  # only a qualified "namespace/slug" token names its namespace
        name |= set(_words(namespace))
    return {
        "name": name,
        "queries": set(w for q in (entry.representative_queries or []) for w in _words(q)),
        "capabilities": capabilities,
        "description": set(_words(entry.description)),
    }


def _spaced(text: str) -> str:
    """Words joined by single spaces with hyphens as spaces, for phrase containment checks."""
    return " ".join(w for word in _words(text) for w in word.split("-") if w)


def _match_quality(token: str, words: set[str]) -> float:
    if token in words:
        return 1.0
    if len(token) >= _MIN_PARTIAL_LEN:
        for word in words:
            if word.startswith(token) or (len(word) >= _MIN_PARTIAL_LEN and token.startswith(word)):
                return 0.7
        for word in words:
            if token in word:
                return 0.4
    return 0.0


def query_tokens(text: str) -> list[str]:
    """Tokens used for ranking: the registry's keyword tokenizer, falling back to raw words."""
    text = _VERSION_SUFFIX_RE.sub(" ", text)
    tokens = [t.replace("_", "-") for t in keyword_tokens(text)]
    if tokens:
        return list(dict.fromkeys(tokens))
    return [w for w in dict.fromkeys(_words(text)) if len(w) >= 2]


def _display_map(text: str, tokens: list[str]) -> dict[str, str]:
    """Map each ranking token back to the word the user actually typed."""
    display: dict[str, str] = {}
    for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", text):
        for token in keyword_tokens(word) or [word.lower()]:
            display.setdefault(token.replace("_", "-"), word)
    return {t: display.get(t, t) for t in tokens}


@dataclass(slots=True)
class Ranked:
    entry: DiscoveryEntry
    score: int
    matched_on: list[str]


def rank_entries(text: str, entries: list[DiscoveryEntry]) -> list[Ranked]:
    """Score entries for ``text`` and return them best-first with stable tie-breaking."""
    tokens = query_tokens(text)
    if not tokens:
        return []
    display = _display_map(text, tokens)
    namespaces = _query_namespaces(text)
    phrase = _spaced(" ".join(tokens))
    ranked: list[Ranked] = []

    for entry in entries:
        fields = _fields(entry, namespaces)
        total = 0.0
        matched: list[str] = []
        for token in tokens:
            best = 0.0
            # The tokenizer stems plural endings; the word as typed must still match exactly.
            variants = {token, display[token].lower().replace("_", "-")}
            for name, weight in _FIELD_WEIGHTS:
                quality = max(_match_quality(v, fields[name]) for v in variants)
                if quality:
                    best = max(best, quality * weight)
            if best:
                total += best
                matched.append(display[token])
        if not matched:
            continue
        relevance = total / len(tokens)
        coverage = len(matched) / len(tokens)
        haystack = _spaced(" ".join([entry.display_name or "", *(entry.representative_queries or [])]))
        namespace, slug = _qualified(entry)
        qualified = _spaced(f"{namespace} {slug}")
        phrase_hit = 1.0 if (len(tokens) == 1 or phrase in haystack or phrase in qualified) else 0.0
        # Weighted so a perfect name match with every token present scores exactly 100.
        score = round(100 * (0.7 * relevance + 0.2 * coverage + 0.1 * phrase_hit))
        ranked.append(Ranked(entry=entry, score=max(1, score), matched_on=matched))

    ranked.sort(
        key=lambda r: (
            -r.score,
            r.entry.lifecycle_status != DiscoveryLifecycle.approved,
            # Equal relevance only: an admin's recommendation breaks the tie, it never moves score.
            not (r.entry.raw_entry or {}).get("obs:recommended"),
            (r.entry.display_name or "").lower(),
            r.entry.ard_identifier,
        )
    )
    return ranked


# ── Pagination ───────────────────────────────────────────────────────────


def _query_fingerprint(text: str, filters: SearchFilters, user_id: str | None) -> str:
    payload = json.dumps(
        {
            "t": text,
            "k": sorted(k.value for k in filters.kinds),
            "m": sorted(filters.media_types),
            "h": sorted(filters.harnesses),
            "g": sorted(filters.tags),
            "c": sorted(filters.capabilities),
            "p": sorted(filters.publishers),
            "v": sorted(filters.versions),
            "forks_of": filters.forks_of,
            "l": sorted(lc.value for lc in filters.lifecycles),
            "a": filters.activatable_only,
            "u": user_id,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def encode_page_token(offset: int, fingerprint: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset, "f": fingerprint}).encode()).decode().rstrip("=")


def decode_page_token(token: str | None, fingerprint: str) -> int:
    if not token:
        return 0
    try:
        padded = token + "=" * (-len(token) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        offset = int(data["o"])
        if data.get("f") != fingerprint or offset < 0:
            raise ValueError
        return offset
    except (ValueError, KeyError, TypeError, OverflowError, json.JSONDecodeError):
        # OverflowError: json.loads accepts 1e999 as infinity and int() then overflows.
        raise InvalidSearchRequestError("invalid pageToken") from None


# ── Search ───────────────────────────────────────────────────────────────


@dataclass(slots=True)
class SearchPage:
    results: list[Ranked]
    next_page_token: str | None
    total: int


def _apply_filters(stmt, filters: SearchFilters):
    if filters.kinds:
        stmt = stmt.where(DiscoveryEntry.kind.in_(list(dict.fromkeys(filters.kinds))))
    if filters.media_types:
        stmt = stmt.where(DiscoveryEntry.media_type.in_(filters.media_types))
    if filters.publishers:
        stmt = stmt.where(DiscoveryEntry.publisher_domain.in_(filters.publishers))
    if filters.versions:
        stmt = stmt.where(DiscoveryEntry.version.in_(filters.versions))
    if filters.activatable_only:
        stmt = stmt.where(DiscoveryEntry.activatable.is_(True))
    return stmt


def _post_filter(entry: DiscoveryEntry, filters: SearchFilters) -> bool:
    """Filters over JSON list columns, applied in Python for dialect portability."""
    if filters.harnesses:
        supported = set(entry.supported_harnesses or [])
        if supported and not (supported & set(filters.harnesses)):
            return False
    if filters.tags:
        tags = {t.lower() for t in (entry.tags or [])}
        if not (tags & set(filters.tags)):
            return False
    if filters.capabilities:
        caps = {c.lower() for c in (entry.capabilities or [])}
        if not (caps & set(filters.capabilities)):
            return False
    return True


async def search_entries(
    db: AsyncSession,
    *,
    text: str,
    filters: SearchFilters | None = None,
    user: Any | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    page_token: str | None = None,
    public_search_enabled: bool = False,
) -> SearchPage:
    """Run a discovery search for ``user``.

    Anonymous callers get results only when ``public_search_enabled`` is set;
    otherwise an empty (still valid) page. ``text`` is required by ARD Search.
    """
    if not isinstance(text, str) or not text.strip():
        raise InvalidSearchRequestError("query.text is required")
    if len(text) > 2000:
        raise InvalidSearchRequestError("query.text is too long")
    page_size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    filters = filters or SearchFilters()
    fingerprint = _query_fingerprint(text, filters, str(user.id) if user is not None else None)
    offset = decode_page_token(page_token, fingerprint)

    if user is None and not public_search_enabled:
        return SearchPage(results=[], next_page_token=None, total=0)

    tokens = query_tokens(text)
    if not tokens:
        return SearchPage(results=[], next_page_token=None, total=0)

    stmt = select(DiscoveryEntry).where(visible_entries_predicate(user, lifecycles=filters.lifecycles))
    stmt = _apply_filters(stmt, filters)

    # search_document stores words split on every non-alphanumeric, so a hyphenated token
    # ("pr-verify") must be prefiltered by its parts; ranking still scores the whole token.
    # Parts of one token are ANDed (so "pr-verify" is not matched by every "pr..."), tokens are ORed.
    def _token_clause(token: str):
        parts = [p for p in re.split(r"[-_]+", token) if p][:_MAX_HYPHEN_PARTS]
        return and_(*(DiscoveryEntry.search_document.like(f"%{escape_like(p)}%") for p in parts))

    stmt = stmt.where(or_(*(_token_clause(t) for t in tokens)))
    stmt = stmt.order_by(DiscoveryEntry.last_seen_at.desc(), DiscoveryEntry.ard_identifier).limit(CANDIDATE_LIMIT)
    candidates = [e for e in (await db.execute(stmt)).scalars().all() if _post_filter(e, filters)]
    if filters.forks_of:
        # A search facet must never reveal team-private forks, even to an admin.
        from services.discovery.forks import public_upstreams

        public_links = await public_upstreams(db, candidates)
        candidates = [
            entry for entry in candidates if public_links.get((entry.kind, entry.local_entity_id)) == filters.forks_of
        ]

    ranked = rank_entries(text, candidates)
    page = ranked[offset : offset + page_size]
    next_token = encode_page_token(offset + page_size, fingerprint) if offset + page_size < len(ranked) else None
    return SearchPage(results=page, next_page_token=next_token, total=len(ranked))
