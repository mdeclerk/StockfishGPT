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
    GameLeaseLostError,
    GameLockedError,
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
async def test_store_contract_set_and_get_roundtrip(kind: str) -> None:
    async with fake_backend(kind) as store:
        original = stored_game_state("game")

        assert isinstance(store, GameStore)
        assert await store.is_ready() is True
        async with store.try_lock("game"):
            await store.set(original)
        assert await store.get("game") == original
        assert await store.get("missing") is None


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_try_lock_rejects_overlap_until_released(
    kind: str,
) -> None:
    async with fake_backend(kind) as store:
        async with store.try_lock("game"):
            with pytest.raises(GameLockedError, match="busy"):
                async with store.try_lock("game"):
                    pass
            async with store.try_lock("other"):
                pass
        async with store.try_lock("game"):
            pass


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_try_lock_releases_on_error_and_cancellation(
    kind: str,
) -> None:
    async with fake_backend(kind) as store:
        with pytest.raises(RuntimeError, match="boom"):
            async with store.try_lock("game"):
                raise RuntimeError("boom")

        held = asyncio.Event()

        async def hold_forever() -> None:
            async with store.try_lock("game"):
                held.set()
                await asyncio.Event().wait()

        holder = asyncio.create_task(hold_forever())
        await held.wait()
        holder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await holder

        async with store.try_lock("game"):
            pass


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_set_requires_the_game_lock(kind: str) -> None:
    async with fake_backend(kind) as store:
        with pytest.raises(RuntimeError, match="try_lock"):
            await store.set(stored_game_state("game"))


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_set_inserts_replaces_and_evicts(kind: str) -> None:
    async with fake_backend(kind, max_games=2) as store:
        async with store.try_lock("first"):
            await store.set(stored_game_state("first"))
        async with store.try_lock("first"):
            await store.set(stored_game_state("first", 1))
        assert await store.get("first") == stored_game_state("first", 1)

        async with store.try_lock("second"):
            await store.set(stored_game_state("second"))
        assert await store.get("first") is not None
        async with store.try_lock("third"):
            await store.set(stored_game_state("third"))

        assert await store.get("second") is None
        assert await store.get("first") == stored_game_state("first", 1)
        assert await store.get("third") == stored_game_state("third")


@pytest.mark.asyncio
async def test_redis_set_is_fenced_against_a_lost_lock_lease() -> None:
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server)
    zombie = RedisGameStore(client, namespace="lease-test")
    successor = RedisGameStore(client, namespace="lease-test")
    try:
        zombie_gate = zombie.try_lock("game")
        await zombie_gate.__aenter__()
        await client.delete(zombie._lock_key("game"))

        async with successor.try_lock("game"):
            await successor.set(stored_game_state("game", 1))
            with pytest.raises(GameLeaseLostError, match="expired"):
                await zombie.set(stored_game_state("game", 2))
            await zombie_gate.__aexit__(None, None, None)
            with pytest.raises(GameLockedError):
                async with zombie.try_lock("game"):
                    pass

        assert await successor.get("game") == stored_game_state("game", 1)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_redis_lock_is_shared_across_store_instances() -> None:
    server = fakeredis.FakeServer()
    first_client = fakeredis.FakeAsyncRedis(server=server)
    second_client = fakeredis.FakeAsyncRedis(server=server)
    first = RedisGameStore(first_client, namespace="cross-instance")
    second = RedisGameStore(second_client, namespace="cross-instance")
    try:
        async with first.try_lock("game"):
            with pytest.raises(GameLockedError):
                async with second.try_lock("game"):
                    pass
        async with second.try_lock("game"):
            pass
    finally:
        await first_client.aclose()
        await second_client.aclose()


@pytest.mark.asyncio
async def test_local_store_uses_sliding_expiration_with_an_injected_clock() -> None:
    clock = ManualClock()
    store = LocalGameStore(ttl_seconds=10, clock=clock)
    record = stored_game_state("game")
    async with store.try_lock("game"):
        await store.set(record)

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
async def test_real_redis_shares_state_locks_ttl_and_lru() -> None:
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
            original = stored_game_state("original")
            async with first_store.try_lock("original"):
                await first_store.set(original)
            assert await second_store.get("original") == original

            async with first_store.try_lock("original"):
                with pytest.raises(GameLockedError):
                    async with second_store.try_lock("original"):
                        pass
                await first_store.set(stored_game_state("original", 1))
            replacement = stored_game_state("original", 1)
            assert await second_store.get("original") == replacement

            other = stored_game_state("other")
            latest = stored_game_state("latest")
            async with first_store.try_lock("other"):
                await first_store.set(other)
            assert await first_store.get("original") is not None
            async with second_store.try_lock("latest"):
                await second_store.set(latest)
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
