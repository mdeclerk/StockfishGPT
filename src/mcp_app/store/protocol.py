"""Game-store capability consumed by the chess service."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol, runtime_checkable

from .models import StoredGameState


@runtime_checkable
class GameStore(Protocol):
    """Atomic storage and per-game mutual exclusion for stored game states."""

    async def create(self, record: StoredGameState) -> bool: ...

    async def get(self, game_id: str) -> StoredGameState | None: ...

    async def compare_and_set(
        self,
        expected: StoredGameState,
        replacement: StoredGameState,
    ) -> bool: ...

    async def set(self, record: StoredGameState) -> None: ...

    def try_lock(self, game_id: str) -> AbstractAsyncContextManager[None]: ...

    async def is_ready(self) -> bool: ...
