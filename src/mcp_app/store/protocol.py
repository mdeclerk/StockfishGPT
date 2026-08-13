"""Game-store capability consumed by the chess service."""

from typing import Protocol, runtime_checkable

from .models import StoredGameState


@runtime_checkable
class GameStore(Protocol):
    """Atomic storage for authoritative stored game states."""

    async def create(self, record: StoredGameState) -> bool: ...

    async def get(self, game_id: str) -> StoredGameState | None: ...

    async def compare_and_set(
        self,
        expected: StoredGameState,
        replacement: StoredGameState,
    ) -> bool: ...

    async def is_ready(self) -> bool: ...
