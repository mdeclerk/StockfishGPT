"""Process-local game storage."""

import asyncio
import secrets
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Self

from .errors import GameLeaseLostError
from .models import StoredGameState

DEFAULT_GAME_TTL_SECONDS = 3600.0
DEFAULT_MAX_GAMES = 1024


@dataclass(slots=True)
class _Entry:
    record: StoredGameState
    last_access: float


class LocalGameStore:
    """Atomic in-memory store with sliding expiration and LRU eviction."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_GAME_TTL_SECONDS,
        max_games: int = DEFAULT_MAX_GAMES,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_games < 1:
            raise ValueError("max_games must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_games = max_games
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._busy: dict[str, str] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the store; local storage owns no external resources."""

    async def is_ready(self) -> bool:
        return True

    async def get(self, game_id: str) -> StoredGameState | None:
        async with self._lock:
            now = self._clock()
            self._purge_expired(now)
            entry = self._entries.get(game_id)
            if entry is None:
                return None
            entry.last_access = now
            self._entries.move_to_end(game_id)
            return entry.record

    async def try_lock(self, game_id: str) -> str | None:
        async with self._lock:
            if game_id in self._busy:
                return None
            fence = secrets.token_urlsafe(16)
            self._busy[game_id] = fence
            return fence

    async def unlock(self, game_id: str, fence: str) -> None:
        async with self._lock:
            if self._busy.get(game_id) == fence:
                del self._busy[game_id]

    async def set(self, record: StoredGameState, fence: str) -> None:
        if self._busy.get(record.game_id) != fence:
            raise GameLeaseLostError(
                f"lock lease for game {record.game_id!r} expired before the write"
            )
        async with self._lock:
            now = self._clock()
            self._purge_expired(now)
            self._entries[record.game_id] = _Entry(record, now)
            self._entries.move_to_end(record.game_id)
            while len(self._entries) > self._max_games:
                self._entries.popitem(last=False)

    def _purge_expired(self, now: float) -> None:
        expired = [
            game_id
            for game_id, entry in self._entries.items()
            if now - entry.last_access > self._ttl_seconds
        ]
        for game_id in expired:
            del self._entries[game_id]
