"""Redis-backed game storage."""

from typing import Any, Self

from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from .errors import StoreDataError, StoreUnavailableError
from .local import DEFAULT_GAME_TTL_SECONDS
from .models import StoredGameState

_RECORD_ADAPTER: TypeAdapter[StoredGameState] = TypeAdapter(StoredGameState)

_CAS_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call('SET', KEYS[1], ARGV[2], 'PX', ARGV[3])
return 1
"""


class RedisGameStore:
    """Shared Redis store with compare-and-set writes and sliding TTLs."""

    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: float = DEFAULT_GAME_TTL_SECONDS,
        namespace: str = "stockfish-gpt",
        close_client: bool = False,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if not namespace:
            raise ValueError("namespace must not be empty")
        self._client = client
        self._ttl_milliseconds = max(1, round(ttl_seconds * 1000))
        self._record_prefix = f"{namespace}:{{games}}:record:"
        self._close_client = close_client

    @classmethod
    def from_url(cls, url: str, **kwargs: Any) -> Self:
        client = Redis.from_url(url, decode_responses=False)
        return cls(client, close_client=True, **kwargs)

    async def __aenter__(self) -> Self:
        try:
            await self._execute(self._client.ping())
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._close_client:
            await self._client.aclose()

    async def is_ready(self) -> bool:
        try:
            return bool(await self._client.ping())
        except RedisError:
            return False

    async def get(self, game_id: str) -> StoredGameState | None:
        payload = await self._execute(
            self._client.getex(self._record_key(game_id), px=self._ttl_milliseconds)
        )
        if payload is None:
            return None
        return self._deserialize(payload)

    async def compare_and_set(
        self,
        expected: StoredGameState | None,
        replacement: StoredGameState,
    ) -> bool:
        key = self._record_key(replacement.game_id)
        payload = self._serialize(replacement)
        if expected is None:
            acquired = await self._execute(
                self._client.set(key, payload, nx=True, px=self._ttl_milliseconds)
            )
            return bool(acquired)
        result = await self._execute(
            self._client.eval(
                _CAS_SCRIPT,
                1,
                key,
                self._serialize(expected),
                payload,
                self._ttl_milliseconds,
            )
        )
        return bool(result)

    def _record_key(self, game_id: str) -> str:
        return f"{self._record_prefix}{game_id}"

    @staticmethod
    async def _execute(awaitable: Any) -> Any:
        try:
            return await awaitable
        except (RedisConnectionError, RedisTimeoutError) as error:
            raise StoreUnavailableError("Redis game store is unavailable") from error

    @staticmethod
    def _serialize(record: StoredGameState) -> bytes:
        return _RECORD_ADAPTER.dump_json(record)

    @staticmethod
    def _deserialize(payload: bytes | str) -> StoredGameState:
        try:
            return _RECORD_ADAPTER.validate_json(payload)
        except ValueError as error:
            raise StoreDataError("stored game state is malformed") from error
