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
    GameRecord,
    GameStore,
    LocalGameStore,
    RedisGameStore,
    StoreDataError,
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


def game_record(game_id: str, version: int = 0) -> GameRecord:
    if version == 0:
        return GameRecord(game_id, 0, "club", (), (None,))
    return GameRecord(
        game_id,
        version,
        "strong",
        ("e2e4", "e7e5"),
        (None, None, StoredOutlook(300, 400, 300)),
    )


@asynccontextmanager
async def fake_backend(
    kind: str,
    *,
    max_games: int = 1024,
) -> AsyncIterator[GameStore]:
    if kind == "local":
        async with LocalGameStore(max_games=max_games) as store:
            yield store
        return

    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server)
    try:
        async with RedisGameStore(client, max_games=max_games) as store:
            yield store
    finally:
        await client.aclose()


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_create_get_and_collision(kind: str) -> None:
    async with fake_backend(kind) as store:
        original = game_record("game")

        assert isinstance(store, GameStore)
        assert await store.is_ready() is True
        assert await store.create(original) is True
        assert await store.create(game_record("game", 1)) is False
        assert await store.get("game") == original
        assert await store.get("missing") is None


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_compare_and_set_has_one_concurrent_winner(
    kind: str,
) -> None:
    async with fake_backend(kind) as store:
        original = game_record("game")
        first = game_record("game", 1)
        second = GameRecord("game", 2, "beginner", (), (None,))
        assert await store.create(original)

        results = await asyncio.gather(
            store.compare_and_set(original, first),
            store.compare_and_set(original, second),
        )

        assert sorted(results) == [False, True]
        assert await store.get("game") in (first, second)
        assert not await store.compare_and_set(original, first)


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_evicts_the_least_recently_used_game(
    kind: str,
) -> None:
    async with fake_backend(kind, max_games=2) as store:
        first = game_record("first")
        second = game_record("second")
        third = game_record("third")
        assert await store.create(first)
        assert await store.create(second)
        assert await store.get("first") == first
        assert await store.create(third)

        assert await store.get("first") == first
        assert await store.get("second") is None
        assert await store.get("third") == third


@pytest.mark.asyncio
async def test_local_store_uses_sliding_expiration_with_an_injected_clock() -> None:
    clock = ManualClock()
    store = LocalGameStore(ttl_seconds=10, clock=clock)
    record = game_record("game")
    assert await store.create(record)

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
    record = game_record("game", 1)
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
async def test_real_redis_shares_atomic_state_ttl_and_lru() -> None:
    assert TEST_REDIS_URL is not None
    namespace = f"stockfish-gpt-test-{uuid.uuid4().hex}"
    first_client = Redis.from_url(TEST_REDIS_URL, decode_responses=False)
    second_client = Redis.from_url(TEST_REDIS_URL, decode_responses=False)
    first_store = RedisGameStore(
        first_client,
        namespace=namespace,
        ttl_seconds=0.25,
        max_games=2,
    )
    second_store = RedisGameStore(
        second_client,
        namespace=namespace,
        ttl_seconds=0.25,
        max_games=2,
    )
    try:
        async with first_store, second_store:
            original = game_record("original")
            assert await first_store.create(original)
            assert await second_store.get("original") == original

            replacements = (game_record("original", 1), game_record("original", 2))
            results = await asyncio.gather(
                first_store.compare_and_set(original, replacements[0]),
                second_store.compare_and_set(original, replacements[1]),
            )
            assert sorted(results) == [False, True]

            other = game_record("other")
            latest = game_record("latest")
            assert await first_store.create(other)
            assert await first_store.get("original") is not None
            assert await second_store.create(latest)
            assert await first_store.get("other") is None

            await asyncio.sleep(0.15)
            assert await second_store.get("latest") == latest
            await asyncio.sleep(0.15)
            assert await first_store.get("latest") == latest
            await asyncio.sleep(0.3)
            assert await first_store.get("latest") is None
    finally:
        keys = [key async for key in first_client.scan_iter(f"{namespace}:*")]
        if keys:
            await first_client.delete(*keys)
        await first_client.aclose()
        await second_client.aclose()
