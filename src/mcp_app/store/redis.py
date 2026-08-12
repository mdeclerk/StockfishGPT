"""Redis-backed game storage."""

import json
from typing import Any, Self

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from .errors import StoreDataError, StoreUnavailableError
from .local import DEFAULT_GAME_TTL_SECONDS, DEFAULT_MAX_GAMES
from .models import GameRecord, StoredOutlook

_SCHEMA_VERSION = 1

_CREATE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
    return 0
end
local created = redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2], 'NX')
if not created then
    return 0
end
local now = redis.call('TIME')
local score = now[1] * 1000000 + now[2]
local previous = tonumber(redis.call('GET', KEYS[3]) or '0')
if score <= previous then
    score = previous + 1
end
redis.call('SET', KEYS[3], score)
redis.call('ZADD', KEYS[2], score, ARGV[4])
while redis.call('ZCARD', KEYS[2]) > tonumber(ARGV[3]) do
    local evicted = redis.call('ZRANGE', KEYS[2], 0, 0)
    if #evicted > 0 then
        redis.call('ZREM', KEYS[2], evicted[1])
        redis.call('DEL', ARGV[5] .. evicted[1])
    end
end
return 1
"""

_GET_SCRIPT = """
local value = redis.call('GET', KEYS[1])
if not value then
    redis.call('ZREM', KEYS[2], ARGV[2])
    return false
end
redis.call('PEXPIRE', KEYS[1], ARGV[1])
local now = redis.call('TIME')
local score = now[1] * 1000000 + now[2]
local previous = tonumber(redis.call('GET', KEYS[3]) or '0')
if score <= previous then
    score = previous + 1
end
redis.call('SET', KEYS[3], score)
redis.call('ZADD', KEYS[2], score, ARGV[2])
return value
"""

_CAS_SCRIPT = """
local value = redis.call('GET', KEYS[1])
if not value then
    redis.call('ZREM', KEYS[2], ARGV[4])
    return 0
end
if value ~= ARGV[1] then
    return 0
end
redis.call('SET', KEYS[1], ARGV[2], 'PX', ARGV[3])
local now = redis.call('TIME')
local score = now[1] * 1000000 + now[2]
local previous = tonumber(redis.call('GET', KEYS[3]) or '0')
if score <= previous then
    score = previous + 1
end
redis.call('SET', KEYS[3], score)
redis.call('ZADD', KEYS[2], score, ARGV[4])
return 1
"""


class RedisGameStore:
    """Shared Redis store with atomic CAS, sliding TTL, and global LRU."""

    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: float = DEFAULT_GAME_TTL_SECONDS,
        max_games: int = DEFAULT_MAX_GAMES,
        namespace: str = "stockfish-gpt",
        close_client: bool = False,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_games < 1:
            raise ValueError("max_games must be positive")
        if not namespace:
            raise ValueError("namespace must not be empty")
        self._client = client
        self._ttl_milliseconds = max(1, round(ttl_seconds * 1000))
        self._max_games = max_games
        base = f"{namespace}:{{games}}"
        self._record_prefix = f"{base}:record:"
        self._lru_key = f"{base}:lru"
        self._clock_key = f"{base}:clock"
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

    async def create(self, record: GameRecord) -> bool:
        payload = self._serialize(record)
        result = await self._execute(
            self._client.eval(
                _CREATE_SCRIPT,
                3,
                self._record_key(record.game_id),
                self._lru_key,
                self._clock_key,
                payload,
                self._ttl_milliseconds,
                self._max_games,
                record.game_id,
                self._record_prefix,
            )
        )
        return bool(result)

    async def get(self, game_id: str) -> GameRecord | None:
        payload = await self._execute(
            self._client.eval(
                _GET_SCRIPT,
                3,
                self._record_key(game_id),
                self._lru_key,
                self._clock_key,
                self._ttl_milliseconds,
                game_id,
            )
        )
        if payload is None:
            return None
        return self._deserialize(payload)

    async def compare_and_set(
        self,
        expected: GameRecord,
        replacement: GameRecord,
    ) -> bool:
        if replacement.game_id != expected.game_id:
            raise ValueError("replacement game_id must match expected game_id")
        result = await self._execute(
            self._client.eval(
                _CAS_SCRIPT,
                3,
                self._record_key(expected.game_id),
                self._lru_key,
                self._clock_key,
                self._serialize(expected),
                self._serialize(replacement),
                self._ttl_milliseconds,
                expected.game_id,
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
    def _serialize(record: GameRecord) -> bytes:
        data = {
            "difficulty": record.difficulty,
            "game_id": record.game_id,
            "outlooks": [
                None
                if outlook is None
                else {
                    "black_wins": outlook.black_wins,
                    "draws": outlook.draws,
                    "white_wins": outlook.white_wins,
                }
                for outlook in record.outlooks
            ],
            "schema": _SCHEMA_VERSION,
            "uci_history": list(record.uci_history),
            "version": record.version,
        }
        return json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()

    @classmethod
    def _deserialize(cls, payload: bytes | str) -> GameRecord:
        try:
            data = json.loads(payload)
            if not isinstance(data, dict) or data.get("schema") != _SCHEMA_VERSION:
                raise ValueError("unsupported game-record schema")
            game_id = cls._string(data, "game_id")
            version = cls._integer(data, "version")
            difficulty = cls._string(data, "difficulty")
            raw_history = data["uci_history"]
            raw_outlooks = data["outlooks"]
            if not isinstance(raw_history, list) or not all(
                isinstance(move, str) for move in raw_history
            ):
                raise ValueError("uci_history must be a list of strings")
            if not isinstance(raw_outlooks, list):
                raise ValueError("outlooks must be a list")
            outlooks = tuple(cls._deserialize_outlook(value) for value in raw_outlooks)
            return GameRecord(
                game_id=game_id,
                version=version,
                difficulty=difficulty,
                uci_history=tuple(raw_history),
                outlooks=outlooks,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise StoreDataError("stored game record is malformed") from error

    @staticmethod
    def _string(data: dict[str, Any], name: str) -> str:
        value = data[name]
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        return value

    @staticmethod
    def _integer(data: dict[str, Any], name: str) -> int:
        value = data[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
        return value

    @classmethod
    def _deserialize_outlook(cls, value: Any) -> StoredOutlook | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("outlook must be an object or null")
        return StoredOutlook(
            white_wins=cls._integer(value, "white_wins"),
            draws=cls._integer(value, "draws"),
            black_wins=cls._integer(value, "black_wins"),
        )
