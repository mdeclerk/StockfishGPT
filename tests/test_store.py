import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import fakeredis
import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from mcp_app.store import (
    GameStore,
    LocalGameStore,
    RedisGameStore,
    StoreDataError,
    StoredGameState,
    StoredOutlook,
    StoreUnavailableError,
)

TEST_REDIS_URL = os.getenv("TEST_REDIS_URL")


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def stored_game_state(game_id: str, version: int = 0) -> StoredGameState:
    if version == 0:
        return StoredGameState(game_id, 0, "club", (), (None,))
    return StoredGameState(
        game_id,
        version,
        "strong",
        ("e2e4", "e7e5"),
        (None, None, StoredOutlook(300, 400, 300)),
    )


@asynccontextmanager
async def fake_backend(kind: str) -> AsyncIterator[GameStore]:
    if kind == "local":
        async with LocalGameStore() as store:
            yield store
        return

    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server)
    try:
        async with RedisGameStore(client) as store:
            yield store
    finally:
        await client.aclose()


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_compare_and_set_and_get_roundtrip(kind: str) -> None:
    async with fake_backend(kind) as store:
        original = stored_game_state("game")

        assert isinstance(store, GameStore)
        assert await store.is_ready() is True
        assert await store.compare_and_set(None, original) is True
        assert await store.get("game") == original
        assert await store.get("missing") is None


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_absent_expectation_inserts_only_once(kind: str) -> None:
    async with fake_backend(kind) as store:
        original = stored_game_state("game")

        assert await store.compare_and_set(None, original) is True
        assert await store.compare_and_set(None, stored_game_state("game", 1)) is False
        assert await store.get("game") == original


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_stale_expectation_leaves_the_record_intact(
    kind: str,
) -> None:
    async with fake_backend(kind) as store:
        stale = stored_game_state("game", 1)

        # Nothing stored yet, so any non-absent expectation must lose.
        assert await store.compare_and_set(stale, stored_game_state("game", 2)) is False

        original = stored_game_state("game")
        assert await store.compare_and_set(None, original) is True
        assert await store.compare_and_set(stale, stored_game_state("game", 2)) is False
        assert await store.get("game") == original


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_compare_and_set_accepts_a_fetched_record(
    kind: str,
) -> None:
    """A record returned by get() must be usable as the next expectation.

    Load-bearing for Redis, which compares serialized bytes rather than
    objects: this pins _serialize(get(x)) == _serialize(x).
    """
    async with fake_backend(kind) as store:
        assert await store.compare_and_set(None, stored_game_state("game")) is True

        fetched = await store.get("game")
        assert fetched is not None

        replacement = stored_game_state("game", 1)
        assert await store.compare_and_set(fetched, replacement) is True
        assert await store.get("game") == replacement


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_replaces_one_game_without_touching_another(
    kind: str,
) -> None:
    async with fake_backend(kind) as store:
        first = stored_game_state("first")
        replacement = stored_game_state("first", 1)

        assert await store.compare_and_set(None, first) is True
        assert await store.compare_and_set(first, replacement) is True
        assert await store.get("first") == replacement

        assert await store.compare_and_set(None, stored_game_state("second")) is True
        assert await store.get("first") == replacement
        assert await store.get("second") == stored_game_state("second")


@pytest.mark.asyncio
async def test_redis_compare_and_set_is_shared_across_store_instances() -> None:
    server = fakeredis.FakeServer()
    first_client = fakeredis.FakeAsyncRedis(server=server)
    second_client = fakeredis.FakeAsyncRedis(server=server)
    first = RedisGameStore(first_client, namespace="cross-instance")
    second = RedisGameStore(second_client, namespace="cross-instance")
    try:
        original = stored_game_state("game")
        winner = stored_game_state("game", 1)
        assert await first.compare_and_set(None, original) is True

        # Both instances raced from the same expectation; only one may commit.
        assert await second.compare_and_set(original, winner) is True
        loser = stored_game_state("game", 2)
        assert await first.compare_and_set(original, loser) is False
        assert await first.get("game") == winner
    finally:
        await first_client.aclose()
        await second_client.aclose()


@pytest.mark.asyncio
async def test_local_store_uses_sliding_expiration_with_an_injected_clock() -> None:
    clock = ManualClock()
    store = LocalGameStore(ttl_seconds=10, clock=clock)
    record = stored_game_state("game")
    assert await store.compare_and_set(None, record) is True

    clock.advance(6)
    assert await store.get("game") == record
    clock.advance(6)
    assert await store.get("game") == record
    clock.advance(11)
    assert await store.get("game") is None


@pytest.mark.asyncio
async def test_redis_store_serializes_records_and_rejects_malformed_data() -> None:
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server)
    store = RedisGameStore(client, namespace="serialization-test")
    record = stored_game_state("game", 1)
    try:
        assert RedisGameStore._deserialize(RedisGameStore._serialize(record)) == record
        await client.set(store._record_key("broken"), b"not-json")
        with pytest.raises(StoreDataError, match="malformed"):
            await store.get("broken")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_redis_store_maps_connection_failures() -> None:
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server)
    store = RedisGameStore(client)
    server.connected = False
    try:
        assert await store.is_ready() is False
        with pytest.raises(StoreUnavailableError, match="unavailable"):
            await store.get("game")
        with pytest.raises(StoreUnavailableError, match="unavailable"):
            async with store:
                pass
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_owned_redis_client_is_closed_when_startup_fails() -> None:
    class BrokenClient:
        def __init__(self) -> None:
            self.closed = False

        async def ping(self) -> bool:
            raise RedisConnectionError("offline")

        async def aclose(self) -> None:
            self.closed = True

    client = BrokenClient()
    store = RedisGameStore(client, close_client=True)

    with pytest.raises(StoreUnavailableError):
        async with store:
            pass

    assert client.closed is True


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    TEST_REDIS_URL is None,
    reason="set TEST_REDIS_URL to exercise a real Redis server",
)
async def test_real_redis_shares_state_and_ttl() -> None:
    assert TEST_REDIS_URL is not None
    namespace = f"stockfish-gpt-test-{uuid.uuid4().hex}"
    first_client = Redis.from_url(TEST_REDIS_URL, decode_responses=False)
    second_client = Redis.from_url(TEST_REDIS_URL, decode_responses=False)
    first_store = RedisGameStore(first_client, namespace=namespace, ttl_seconds=0.25)
    second_store = RedisGameStore(second_client, namespace=namespace, ttl_seconds=0.25)
    try:
        async with first_store, second_store:
            original = stored_game_state("original")
            assert await first_store.compare_and_set(None, original) is True
            assert await second_store.get("original") == original

            replacement = stored_game_state("original", 1)
            assert await second_store.compare_and_set(original, replacement) is True
            assert await first_store.compare_and_set(original, replacement) is False
            assert await first_store.get("original") == replacement

            await asyncio.sleep(0.15)
            assert await second_store.get("original") == replacement
            await asyncio.sleep(0.15)
            assert await first_store.get("original") == replacement
            await asyncio.sleep(0.3)
            assert await first_store.get("original") is None
    finally:
        keys = [key async for key in first_client.scan_iter(f"{namespace}:*")]
        if keys:
            await first_client.delete(*keys)
        await first_client.aclose()
        await second_client.aclose()
