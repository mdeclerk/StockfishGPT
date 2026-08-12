"""Engine capability consumed by the chess service."""

from typing import Protocol, runtime_checkable

import chess
import chess.engine


@runtime_checkable
class Engine(Protocol):
    """Raw engine analysis and liveness."""

    @property
    def is_alive(self) -> bool: ...

    async def analyze(
        self,
        board: chess.Board,
        *,
        multipv: int,
        nodes: int,
    ) -> list[chess.engine.InfoDict]: ...
