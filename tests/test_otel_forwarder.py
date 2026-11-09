# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The forwarder's invariants for logs: every row up to the checkpoint arrives once, in order,
across chunks, restarts, outages, final failures and integrity rewinds."""

from __future__ import annotations

import gzip
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest

import services.ssrf_guard as ssrf_guard
from services.otel import forwarder
from services.otel.destinations import Destination
from services.otel.forwarder import ForwardState, enqueue_forwarding, forward_session_logs, job_id
from services.otel.types import SessionKey

KEY = SessionKey(project_id="default", user_id="user-1", harness="claude-code", session_id="sess-1")


def _destination(**overrides) -> Destination:
    values = {
        "id": "collector",
        "url": "https://otel.example.com",
        "include_content": True,
        "added_at": "2026-09-01T00:00:00Z",
    }
    values.update(overrides)
    return Destination(**values)


def _rows(count: int) -> list[dict]:
    return [
        {
            "session_id": KEY.session_id,
            "user_id": KEY.user_id,
            "harness": KEY.harness,
            "line_offset": offset,
            "line_hash": f"h{offset}",
            "is_source_record": 1,
            "event_type": "assistant_text",
            "timestamp": "2026-09-30 10:00:00.000",
            "ingested_at": "2026-09-30 10:00:01.000",
            "raw_line": f'{{"line":{offset}}}',
        }
        for offset in range(count)
    ]


class FakeStore:
    """In-memory stand-in for ClickHouse and Redis."""

    def __init__(self, rows: list[dict], checkpoint: int | list[int], first_event: datetime | None = None):
        self.all_rows = rows
        self.checkpoints = list(checkpoint) if isinstance(checkpoint, list) else [checkpoint]
        self.first = first_event or datetime(2026, 9, 30, tzinfo=UTC)
        self.states: dict[tuple, ForwardState] = {}
        self.state_writes: list[ForwardState] = []
        self.paused: dict[str, int | None] = {}
        self.held: set[str] = set()
        self.deliveries: list[dict] = []

    @asynccontextmanager
    async def locked(self, name):
        if name in self.held:
            yield False
            return
        self.held.add(name)
        try:
            yield True
        finally:
            self.held.discard(name)

    async def is_paused(self, destination_id):
        return destination_id in self.paused

    async def pause(self, destination_id, status_code):
        self.paused[destination_id] = status_code

    async def checkpoint(self, key):
        # Each read takes the next value; the last one sticks.
        return self.checkpoints.pop(0) if len(self.checkpoints) > 1 else self.checkpoints[0]

    async def state(self, destination_id, signal, key):
        return self.states.get((destination_id, signal, key))

    async def set_state(self, destination_id, signal, key, state):
        self.states[(destination_id, signal, key)] = state
        self.state_writes.append(state)

    async def first_event(self, key):
        return self.first

    async def rows(self, key, *, after_line, up_to_line):
        return [row for row in self.all_rows if after_line < row["line_offset"] <= up_to_line]

    async def record_deliveries(self, records):
        self.deliveries.extend(records)


class Endpoint:
    """A mock OTLP/HTTP endpoint that decodes what it receives."""

    def __init__(self, *statuses: int):
        self.statuses = list(statuses)
        self.requests: list[httpx.Request] = []
        self.received: list[dict] = []  # one entry per delivered record

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status = self.statuses.pop(0) if self.statuses else 200
        if 200 <= status < 300:
            message = ExportLogsServiceRequest.FromString(gzip.decompress(request.content))
            for record in message.resource_logs[0].scope_logs[0].log_records:
                attributes = {a.key: a.value for a in record.attributes}
                self.received.append(
                    {
                        "offset": attributes["observal.line_offset"].int_value,
                        "body": record.body.string_value if record.HasField("body") else None,
                        "replay": "observal.forward.replay" in attributes,
                    }
                )
        return httpx.Response(status)

    @property
    def offsets(self) -> list[int]:
        return [item["offset"] for item in self.received]


async def _no_sleep(_seconds):
    return None


async def _forward(store: FakeStore, endpoint: Endpoint, destination: Destination | None = None):
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint.handler)) as client:
        return await forward_session_logs(
            destination or _destination(), KEY, store=store, client=client, sleep=_no_sleep
        )


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(ssrf_guard, "is_private_url", lambda url: False)


async def test_every_row_up_to_the_checkpoint_arrives_once_with_its_stored_body():
    rows = _rows(10)
    store, endpoint = FakeStore(rows, checkpoint=9), Endpoint()

    result = await _forward(store, endpoint)

    assert result.status == "caught_up" and result.records_sent == 10
    assert endpoint.offsets == list(range(10))
    assert [item["body"] for item in endpoint.received] == [row["raw_line"] for row in rows]
    assert store.states[("collector", "logs", KEY)].forwarded_line == 9


async def test_rows_above_the_checkpoint_wait():
    store, endpoint = FakeStore(_rows(10), checkpoint=4), Endpoint()

    await _forward(store, endpoint)

    assert endpoint.offsets == [0, 1, 2, 3, 4]


async def test_a_second_run_sends_nothing_new():
    store, endpoint = FakeStore(_rows(5), checkpoint=4), Endpoint()

    await _forward(store, endpoint)
    result = await _forward(store, endpoint)

    assert result.status == "caught_up" and result.records_sent == 0
    assert endpoint.offsets == [0, 1, 2, 3, 4]
    assert len(endpoint.requests) == 1


async def test_rows_that_arrive_during_a_run_are_picked_up_before_it_ends():
    # The checkpoint moves from 3 to 7 between reads, as a concurrent push would move it.
    store, endpoint = FakeStore(_rows(8), checkpoint=[3, 7]), Endpoint()

    result = await _forward(store, endpoint)

    assert result.records_sent == 8
    assert endpoint.offsets == list(range(8))


async def test_windows_and_chunks_move_the_watermark_after_each_2xx(monkeypatch):
    monkeypatch.setattr(forwarder, "WINDOW_LINES", 4)
    monkeypatch.setattr(forwarder, "MAX_CHUNK_RECORDS", 3)
    store, endpoint = FakeStore(_rows(10), checkpoint=9), Endpoint()

    await _forward(store, endpoint)

    assert endpoint.offsets == list(range(10))
    assert [state.forwarded_line for state in store.state_writes] == [2, 3, 6, 7, 9]


async def test_chunks_are_capped_by_size(monkeypatch):
    monkeypatch.setattr(forwarder, "MAX_CHUNK_BYTES", 1100)  # two ~520 byte rows per chunk
    store, endpoint = FakeStore(_rows(5), checkpoint=4), Endpoint()

    await _forward(store, endpoint)

    assert len(endpoint.requests) == 3
    assert endpoint.offsets == list(range(5))


async def test_an_outage_loses_nothing_and_duplicates_nothing():
    store = FakeStore(_rows(6), checkpoint=5)
    down = Endpoint(*[503] * 5)

    first = await _forward(store, down)
    assert first.status == "retry_later"
    assert ("collector", "logs", KEY) not in store.states
    assert len(down.requests) == 5

    up = Endpoint()
    second = await _forward(store, up)
    assert second.status == "caught_up"
    assert up.offsets == list(range(6))


async def test_a_failure_mid_session_resumes_after_the_last_good_chunk(monkeypatch):
    monkeypatch.setattr(forwarder, "MAX_CHUNK_RECORDS", 2)
    store = FakeStore(_rows(6), checkpoint=5)
    flaky = Endpoint(200, *[503] * 5)

    await _forward(store, flaky)
    assert flaky.offsets == [0, 1]
    assert store.states[("collector", "logs", KEY)].forwarded_line == 1

    up = Endpoint()
    await _forward(store, up)
    assert up.offsets == [2, 3, 4, 5]


async def test_a_final_rejection_pauses_the_destination_and_others_keep_flowing():
    store = FakeStore(_rows(3), checkpoint=2)
    rejecting = Endpoint(401)

    result = await _forward(store, rejecting)
    assert result.status == "failed"
    assert store.paused == {"collector": 401}

    again = await _forward(store, Endpoint())
    assert again.status == "paused"

    other = Endpoint()
    result = await _forward(store, other, _destination(id="langfuse"))
    assert result.status == "caught_up"
    assert other.offsets == [0, 1, 2]


async def test_an_integrity_rewind_resends_the_range_marked_as_replay():
    store = FakeStore(_rows(12), checkpoint=9)
    await _forward(store, Endpoint())

    # An integrity repair moves the checkpoint back to 5; lines 6..9 may be replaced.
    store.checkpoints = [5]
    rewound = await _forward(store, Endpoint())
    assert rewound.status == "caught_up"
    assert store.states[("collector", "logs", KEY)] == ForwardState(forwarded_line=5, replay_through=9)

    # The client re-sends; the checkpoint passes the old watermark.
    store.checkpoints = [11]
    endpoint = Endpoint()
    await _forward(store, endpoint)
    assert endpoint.offsets == [6, 7, 8, 9, 10, 11]
    assert [item["replay"] for item in endpoint.received] == [True, True, True, True, False, False]
    # Replayed and new rows never share a request.
    assert len(endpoint.requests) == 2


async def test_a_session_already_running_when_the_destination_was_added_is_skipped():
    store = FakeStore(_rows(3), checkpoint=2, first_event=datetime(2026, 8, 1, tzinfo=UTC))
    endpoint = Endpoint()

    result = await _forward(store, endpoint)

    assert result.status == "excluded"
    assert endpoint.requests == []


async def test_without_added_at_every_session_is_forwarded():
    store = FakeStore(_rows(2), checkpoint=1, first_event=datetime(2020, 1, 1, tzinfo=UTC))
    endpoint = Endpoint()

    await _forward(store, endpoint, _destination(added_at=None))

    assert endpoint.offsets == [0, 1]


async def test_a_held_lock_means_another_run_has_it():
    store, endpoint = FakeStore(_rows(2), checkpoint=1), Endpoint()
    store.held.add(forwarder._lock_name("collector", "logs", KEY))

    result = await _forward(store, endpoint)

    assert result.status == "locked"
    assert endpoint.requests == []


async def test_nothing_acknowledged_yet_sends_nothing():
    store, endpoint = FakeStore([], checkpoint=-1), Endpoint()

    result = await _forward(store, endpoint)

    assert result.status == "caught_up"
    assert endpoint.requests == []


async def test_content_gate_holds_through_the_forwarder():
    store, endpoint = FakeStore(_rows(2), checkpoint=1), Endpoint()

    await _forward(store, endpoint, _destination(include_content=False))

    assert [item["body"] for item in endpoint.received] == [None, None]


async def test_missing_rows_below_the_checkpoint_stop_the_run_without_moving_the_watermark():
    store, endpoint = FakeStore([], checkpoint=3), Endpoint()

    result = await _forward(store, endpoint)

    assert result.status == "retry_later"
    assert store.state_writes == []


async def test_each_attempt_is_recorded_without_url_or_headers():
    store = FakeStore(_rows(2), checkpoint=1)

    await _forward(store, Endpoint(503, 200), _destination(headers={"Authorization": "Bearer secret"}))

    assert [d["status"] for d in store.deliveries] == ["retry", "delivered"]
    assert all(d["destination_id"] == "collector" and d["session_id"] == "sess-1" for d in store.deliveries)
    assert all(d["records"] == 2 for d in store.deliveries)
    flattened = repr(store.deliveries)
    assert "otel.example.com" not in flattened
    assert "secret" not in flattened


# ── Queueing ─────────────────────────────────────────────────────────────


class FakePool:
    def __init__(self):
        self.jobs: list[tuple] = []

    async def enqueue_job(self, name, *args, _job_id=None):
        self.jobs.append((name, args, _job_id))


async def test_enqueue_queues_one_job_per_enabled_destination(monkeypatch):
    destinations = [_destination(), _destination(id="off", enabled=False), _destination(id="langfuse")]
    monkeypatch.setattr("services.otel.destinations.load_destinations", _returning(destinations))
    pool = FakePool()

    queued = await enqueue_forwarding(KEY, pool=pool)

    assert queued == 2
    assert pool.jobs == [
        ("forward_session", ("collector", "default", "user-1", "claude-code", "sess-1"), job_id("collector", KEY)),
        ("forward_session", ("langfuse", "default", "user-1", "claude-code", "sess-1"), job_id("langfuse", KEY)),
    ]


async def test_enqueue_does_nothing_without_destinations(monkeypatch):
    monkeypatch.setattr("services.otel.destinations.load_destinations", _returning([]))

    def no_pool():
        raise AssertionError("the arq pool must not be touched")

    monkeypatch.setattr("services.redis._get_arq_pool", no_pool)

    assert await enqueue_forwarding(KEY) == 0


def _returning(value):
    async def fake():
        return value

    return fake


# ── The real store's Redis use ───────────────────────────────────────────


class StubRedis:
    def __init__(self):
        self.values: dict[str, tuple[str, int | None]] = {}
        self.deleted: list[str] = []
        self.lock_args: dict = {}
        self.acquired = True

    async def exists(self, name):
        return int(name in self.values)

    async def set(self, name, value, ex=None):
        self.values[name] = (value, ex)

    async def delete(self, *names):
        self.deleted.extend(names)
        for name in names:
            self.values.pop(name, None)

    def lock(self, name, **kwargs):
        self.lock_args = {"name": name, **kwargs}
        stub = self

        class Lock:
            async def acquire(self):
                return stub.acquired

            async def release(self):
                stub.released = True

        return Lock()


@pytest.fixture
def stub_redis(monkeypatch):
    stub = StubRedis()
    monkeypatch.setattr("services.redis.get_redis", lambda: stub)
    return stub


async def test_store_pauses_expire_and_saving_lifts_them(stub_redis):
    store = forwarder.ForwardStore()

    await store.pause("collector", 401)
    assert await store.is_paused("collector")
    assert stub_redis.values["otlp:paused:collector"] == ("401", forwarder.PAUSE_TTL_SECONDS)

    await forwarder.clear_pauses(["collector", "langfuse"])
    assert not await store.is_paused("collector")
    assert stub_redis.deleted == ["otlp:paused:collector", "otlp:paused:langfuse"]


async def test_store_lock_never_waits_and_outlives_the_job_timeout(stub_redis):
    store = forwarder.ForwardStore()

    async with store.locked("otlp:lock:x") as acquired:
        assert acquired
    assert stub_redis.lock_args == {"name": "otlp:lock:x", "timeout": forwarder.LOCK_TTL_SECONDS, "blocking": False}
    assert forwarder.LOCK_TTL_SECONDS > 120  # the forward job's arq timeout
    assert stub_redis.released

    stub_redis.acquired = False
    stub_redis.released = False
    async with store.locked("otlp:lock:x") as acquired:
        assert not acquired
    assert not stub_redis.released
