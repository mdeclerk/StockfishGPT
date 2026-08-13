"""Game-store capability consumed by the chess service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol, runtime_checkable

from .errors import GameLockedError
from .models import StoredGameState


@runtime_checkable
class GameStore(Protocol):
    """Atomic storage and per-game mutual exclusion for stored game states."""

    async def get(self, game_id: str) -> StoredGameState | None: ...

    async def set(self, record: StoredGameState, fence: str) -> None: ...

    async def try_lock(self, game_id: str) -> str | None: ...

    async def unlock(self, game_id: str, fence: str) -> None: ...

    async def is_ready(self) -> bool: ...


@asynccontextmanager
async def locked(store: GameStore, game_id: str) -> AsyncIterator[str]:
    """Hold ``game_id``'s lock for the block, releasing it on exit.

    Yields the fence token the caller must pass to ``set()`` to prove it
    still holds the lock; threading it through the caller's own scope (rather
    than caching it on the store, keyed only by ``game_id``) keeps two
    overlapping acquisitions of the same game on one store instance —
    e.g. a stalled holder and the successor that took over after its lease
    expired — from being confused with one another.
    """
    fence = await store.try_lock(game_id)
    if fence is None:
        raise GameLockedError(f"game {game_id!r} is busy")
    try:
        yield fence
    finally:
        await store.unlock(game_id, fence)
