"""Raw analysis-engine doubles shared by backend tests."""

from typing import Self

import chess
import chess.engine


def candidate_info(
    move: chess.Move,
    *,
    centipawns: int = 15,
    white_wdl: tuple[int, int, int] = (300, 400, 300),
) -> chess.engine.InfoDict:
    return {
        "score": chess.engine.PovScore(chess.engine.Cp(centipawns), chess.WHITE),
        "wdl": chess.engine.PovWdl(chess.engine.Wdl(*white_wdl), chess.WHITE),
        "pv": [move],
    }


class FirstMoveEngine:
    """Return the first legal moves as raw python-chess analysis."""

    is_alive = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    async def analyze(
        self,
        board: chess.Board,
        *,
        multipv: int,
        nodes: int,
    ) -> list[chess.engine.InfoDict]:
        self.calls.append((board.fen(en_passant="fen"), multipv, nodes))
        return [
            candidate_info(move) for move in list(board.legal_moves)[:multipv]
        ]


class RecordingEngine(FirstMoveEngine):
    """Record lifecycle calls and raw analyses."""

    def __init__(self) -> None:
        super().__init__()
        self.starts = 0
        self.stops = 0
        self.running = False

    @property
    def is_alive(self) -> bool:
        return self.running

    async def start(self) -> None:
        self.starts += 1
        self.running = True

    async def close(self) -> None:
        self.stops += 1
        self.running = False

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
