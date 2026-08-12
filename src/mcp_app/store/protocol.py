"""Game-store capability consumed by the chess service."""

from typing import Protocol, runtime_checkable

from .models import GameRecord


@runtime_checkable
class GameStore(Protocol):
    """Atomic storage for authoritative game records."""

    async def create(self, record: GameRecord) -> bool: ...

    async def get(self, game_id: str) -> GameRecord | None: ...

    async def compare_and_set(
        self,
        expected: GameRecord,
        replacement: GameRecord,
    ) -> bool: ...

    async def is_ready(self) -> bool: ...
