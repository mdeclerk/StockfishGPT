"""Process-local game storage."""

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Self

from .models import StoredGameState

DEFAULT_GAME_TTL_SECONDS = 3600.0


@dataclass(slots=True)
class _Entry:
    record: StoredGameState
    last_access: float


class LocalGameStore:
    """Atomic in-memory store with sliding expiration."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_GAME_TTL_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: dict[str, _Entry] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the store; local storage owns no external resources."""

    async def is_ready(self) -> bool:
        return True

    async def get(self, game_id: str) -> StoredGameState | None:
        now = self._clock()
        self._purge_expired(now)
        entry = self._entries.get(game_id)
        if entry is None:
            return None
        entry.last_access = now
        return entry.record

    async def compare_and_set(
        self,
        expected: StoredGameState | None,
        replacement: StoredGameState,
    ) -> bool:
        now = self._clock()
        self._purge_expired(now)
        entry = self._entries.get(replacement.game_id)
        if (entry.record if entry else None) != expected:
            return False
        self._entries[replacement.game_id] = _Entry(replacement, now)
        return True

    def _purge_expired(self, now: float) -> None:
        expired = [
            game_id
            for game_id, entry in self._entries.items()
            if now - entry.last_access > self._ttl_seconds
        ]
        for game_id in expired:
            del self._entries[game_id]
