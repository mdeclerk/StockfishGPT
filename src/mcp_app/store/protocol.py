"""Game-store capability consumed by the chess service."""

from typing import Protocol, runtime_checkable

from .models import StoredGameState


@runtime_checkable
class GameStore(Protocol):
    """Atomic compare-and-set storage for stored game states."""

    async def get(self, game_id: str) -> StoredGameState | None: ...

    async def compare_and_set(
        self,
        expected: StoredGameState | None,
        replacement: StoredGameState,
    ) -> bool: ...

    async def is_ready(self) -> bool: ...
