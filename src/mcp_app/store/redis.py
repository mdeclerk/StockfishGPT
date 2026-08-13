"""Redis-backed game storage."""

import json
import secrets
from contextlib import suppress
from typing import Any, Self

from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from .errors import (
    GameLeaseLostError,
    StoreDataError,
    StoreUnavailableError,
)
from .local import DEFAULT_GAME_TTL_SECONDS
from .models import StoredGameState

# A lease must outlast the longest request that holds it, or a slow engine
# analysis commits under a lock a successor already took over. The default
# leaves ample room above the engine's own per-operation timeout.
DEFAULT_LOCK_TTL_SECONDS = 30.0

# A rolling deploy points two code versions at one keyspace. Stamping the
# schema turns a mismatch into a clean StoreDataError instead of a silent
# misread when a field's meaning changes without its shape.
_SCHEMA_VERSION = 1
_RECORD_ADAPTER: TypeAdapter[StoredGameState] = TypeAdapter(StoredGameState)

_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('DEL', KEYS[1])
end
return 1
"""

_SET_SCRIPT = """
if redis.call('GET', KEYS[2]) ~= ARGV[1] then
    return 0
end
redis.call('SET', KEYS[1], ARGV[2], 'PX', ARGV[3])
return 1
"""


class RedisGameStore:
    """Shared Redis store with per-game locks, fenced writes, and sliding TTLs."""

    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: float = DEFAULT_GAME_TTL_SECONDS,
        lock_ttl_seconds: float = DEFAULT_LOCK_TTL_SECONDS,
        namespace: str = "stockfish-gpt",
        close_client: bool = False,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if lock_ttl_seconds <= 0:
            raise ValueError("lock_ttl_seconds must be positive")
        if not namespace:
            raise ValueError("namespace must not be empty")
        self._client = client
        self._ttl_milliseconds = max(1, round(ttl_seconds * 1000))
        self._lock_ttl_milliseconds = max(1, round(lock_ttl_seconds * 1000))
        base = f"{namespace}:{{games}}"
        self._record_prefix = f"{base}:record:"
        self._lock_prefix = f"{base}:lock:"
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

    async def try_lock(self, game_id: str) -> str | None:
        fence = secrets.token_urlsafe(16)
        acquired = await self._execute(
            self._client.set(
                self._lock_key(game_id),
                fence,
                nx=True,
                px=self._lock_ttl_milliseconds,
            )
        )
        if not acquired:
            return None
        return fence

    async def unlock(self, game_id: str, fence: str) -> None:
        # The lease TTL reclaims a lock whose release did not reach Redis.
        # The release script only deletes the key if it still holds this
        # exact fence, so a late unlock() from a caller whose lease already
        # expired can't clobber whoever's lock replaced it.
        with suppress(StoreUnavailableError):
            await self._execute(
                self._client.eval(
                    _RELEASE_SCRIPT,
                    1,
                    self._lock_key(game_id),
                    fence,
                )
            )

    async def set(self, record: StoredGameState, fence: str) -> None:
        result = await self._execute(
            self._client.eval(
                _SET_SCRIPT,
                2,
                self._record_key(record.game_id),
                self._lock_key(record.game_id),
                fence,
                self._serialize(record),
                self._ttl_milliseconds,
            )
        )
        if not result:
            raise GameLeaseLostError(
                f"lock lease for game {record.game_id!r} expired before the write"
            )

    def _record_key(self, game_id: str) -> str:
        return f"{self._record_prefix}{game_id}"

    def _lock_key(self, game_id: str) -> str:
        return f"{self._lock_prefix}{game_id}"

    @staticmethod
    async def _execute(awaitable: Any) -> Any:
        try:
            return await awaitable
        except (RedisConnectionError, RedisTimeoutError) as error:
            raise StoreUnavailableError("Redis game store is unavailable") from error

    @staticmethod
    def _serialize(record: StoredGameState) -> bytes:
        data = _RECORD_ADAPTER.dump_python(record, mode="json")
        data["schema"] = _SCHEMA_VERSION
        return json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()

    @staticmethod
    def _deserialize(payload: bytes | str) -> StoredGameState:
        try:
            data = json.loads(payload)
            is_current_schema = (
                isinstance(data, dict) and data.pop("schema", None) == _SCHEMA_VERSION
            )
            if not is_current_schema:
                raise ValueError("unsupported stored-game-state schema")
            return _RECORD_ADAPTER.validate_python(data)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise StoreDataError("stored game state is malformed") from error
