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
    locked,
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
async def test_store_contract_set_and_get_roundtrip(kind: str) -> None:
    async with fake_backend(kind) as store:
        original = stored_game_state("game")

        assert isinstance(store, GameStore)
        assert await store.is_ready() is True
        async with locked(store, "game") as fence:
            await store.set(original, fence)
        assert await store.get("game") == original
        assert await store.get("missing") is None


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_try_lock_rejects_overlap_until_released(
    kind: str,
) -> None:
    async with fake_backend(kind) as store:
        fence = await store.try_lock("game")
        assert fence is not None
        assert await store.try_lock("game") is None
        other_fence = await store.try_lock("other")
        assert other_fence is not None
        await store.unlock("other", other_fence)

        with pytest.raises(GameLockedError, match="busy"):
            async with locked(store, "game"):
                pass

        await store.unlock("game", fence)
        async with locked(store, "game"):
            pass


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_try_lock_releases_on_error_and_cancellation(
    kind: str,
) -> None:
    async with fake_backend(kind) as store:
        with pytest.raises(RuntimeError, match="boom"):
            async with locked(store, "game"):
                raise RuntimeError("boom")

        held = asyncio.Event()

        async def hold_forever() -> None:
            async with locked(store, "game"):
                held.set()
                await asyncio.Event().wait()

        holder = asyncio.create_task(hold_forever())
        await held.wait()
        holder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await holder

        async with locked(store, "game"):
            pass


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_set_requires_the_game_lock(kind: str) -> None:
    async with fake_backend(kind) as store:
        with pytest.raises(GameLeaseLostError, match="expired"):
            await store.set(stored_game_state("game"), "not-a-real-fence")


@pytest.mark.parametrize("kind", ["local", "redis"])
@pytest.mark.asyncio
async def test_store_contract_set_inserts_and_replaces(kind: str) -> None:
    async with fake_backend(kind) as store:
        async with locked(store, "first") as fence:
            await store.set(stored_game_state("first"), fence)
        async with locked(store, "first") as fence:
            await store.set(stored_game_state("first", 1), fence)
        assert await store.get("first") == stored_game_state("first", 1)

        async with locked(store, "second") as fence:
            await store.set(stored_game_state("second"), fence)
        assert await store.get("first") == stored_game_state("first", 1)
        assert await store.get("second") == stored_game_state("second")


@pytest.mark.asyncio
async def test_redis_set_is_fenced_against_a_lost_lock_lease() -> None:
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server)
    zombie = RedisGameStore(client, namespace="lease-test")
    successor = RedisGameStore(client, namespace="lease-test")
    try:
        zombie_fence = await zombie.try_lock("game")
        assert zombie_fence is not None
        await client.delete(zombie._lock_key("game"))

        async with locked(successor, "game") as successor_fence:
            await successor.set(stored_game_state("game", 1), successor_fence)
            with pytest.raises(GameLeaseLostError, match="expired"):
                await zombie.set(stored_game_state("game", 2), zombie_fence)
            await zombie.unlock("game", zombie_fence)
            assert await zombie.try_lock("game") is None

        assert await successor.get("game") == stored_game_state("game", 1)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_redis_zombie_fence_ignored_after_successor_takes_over() -> None:
    """A zombie's late unlock()/set() must not clobber the successor's lock.

    The fence must be scoped per-acquisition, not cached by game_id.
    """
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server)
    store = RedisGameStore(client, namespace="single-instance-lease-test")
    try:
        zombie_fence = await store.try_lock("game")
        assert zombie_fence is not None
        await client.delete(store._lock_key("game"))  # simulate lease expiry

        successor_fence = await store.try_lock("game")
        assert successor_fence is not None
        assert successor_fence != zombie_fence
        await store.set(stored_game_state("game", 1), successor_fence)

        with pytest.raises(GameLeaseLostError, match="expired"):
            await store.set(stored_game_state("game", 2), zombie_fence)
        await store.unlock("game", zombie_fence)

        assert await store.get("game") == stored_game_state("game", 1)
        assert await store.try_lock("game") is None  # successor's lock still held
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
        async with locked(first, "game"):
            with pytest.raises(GameLockedError):
                async with locked(second, "game"):
                    pass
        async with locked(second, "game"):
            pass
    finally:
        await first_client.aclose()
        await second_client.aclose()


@pytest.mark.asyncio
async def test_local_store_uses_sliding_expiration_with_an_injected_clock() -> None:
    clock = ManualClock()
    store = LocalGameStore(ttl_seconds=10, clock=clock)
    record = stored_game_state("game")
    async with locked(store, "game") as fence:
        await store.set(record, fence)

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
async def test_real_redis_shares_state_locks_and_ttl() -> None:
    assert TEST_REDIS_URL is not None
    namespace = f"stockfish-gpt-test-{uuid.uuid4().hex}"
    first_client = Redis.from_url(TEST_REDIS_URL, decode_responses=False)
    second_client = Redis.from_url(TEST_REDIS_URL, decode_responses=False)
    first_store = RedisGameStore(first_client, namespace=namespace, ttl_seconds=0.25)
    second_store = RedisGameStore(second_client, namespace=namespace, ttl_seconds=0.25)
    try:
        async with first_store, second_store:
            original = stored_game_state("original")
            async with locked(first_store, "original") as fence:
                await first_store.set(original, fence)
            assert await second_store.get("original") == original

            async with locked(first_store, "original") as fence:
                with pytest.raises(GameLockedError):
                    async with locked(second_store, "original"):
                        pass
                await first_store.set(stored_game_state("original", 1), fence)
            replacement = stored_game_state("original", 1)
            assert await second_store.get("original") == replacement

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
