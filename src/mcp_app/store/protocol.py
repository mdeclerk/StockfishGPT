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

    async def set(self, record: StoredGameState) -> None: ...

    async def try_lock(self, game_id: str) -> bool: ...

    async def unlock(self, game_id: str) -> None: ...

    async def is_ready(self) -> bool: ...


@asynccontextmanager
async def locked(store: GameStore, game_id: str) -> AsyncIterator[None]:
    """Hold ``game_id``'s lock for the block, releasing it on exit."""
    if not await store.try_lock(game_id):
        raise GameLockedError(f"game {game_id!r} is busy")
    try:
        yield
    finally:
        await store.unlock(game_id)
