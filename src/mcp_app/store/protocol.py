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
    """Hold ``game_id``'s lock for the block, yielding the fence token for ``set()``.

    Scoping the fence to the caller, not caching it on the store, keeps a
    stalled holder from being confused with the successor that took over.
    """
    fence = await store.try_lock(game_id)
    if fence is None:
        raise GameLockedError(f"game {game_id!r} is busy")
    try:
        yield fence
    finally:
        await store.unlock(game_id, fence)
