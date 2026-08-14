"""Store-backed chess games and Stockfish-backed behavior."""

import math
import random
import secrets
from dataclasses import dataclass, replace

import chess

from mcp_app.engine import Engine
from mcp_app.store import (
    GameStore,
    StoreDataError,
    StoredGameState,
)

from .errors import (
    GameNotFoundError,
    GameVersionError,
    InvalidMoveError,
    NothingToUndoError,
    NotPlayersTurnError,
    TerminalPositionError,
)
from .models import (
    Difficulty,
    Evaluation,
    GameState,
    GameStatus,
    Move,
    PositionAnalysis,
    ServiceStatus,
)

_ADVICE_CANDIDATES = 3
_ANALYSIS_NODES = 60_000
_MOVE_NODES = 80_000


@dataclass(frozen=True, slots=True)
class _DifficultyPreset:
    candidates: int
    maximum_loss: float
    temperature: float


_PRESETS = {
    Difficulty.BEGINNER: _DifficultyPreset(5, 0.20, 0.10),
    Difficulty.CLUB: _DifficultyPreset(3, 0.08, 0.035),
    Difficulty.STRONG: _DifficultyPreset(1, 0.0, 0.0),
}


class ChessService:
    """Run store- and engine-backed chess use cases."""

    def __init__(self, engine: Engine, store: GameStore) -> None:
        self._engine = engine
        self._store = store

    async def health_status(self) -> ServiceStatus:
        if not self._engine.is_alive:
            return ServiceStatus.ENGINE_UNAVAILABLE
        if not await self._store.is_ready():
            return ServiceStatus.STORE_UNAVAILABLE
        return ServiceStatus.OK

    async def start_game(
        self,
        difficulty: Difficulty = Difficulty.CLUB,
    ) -> GameState:
        while True:
            record = StoredGameState(
                game_id=self._new_game_id(),
                version=0,
                difficulty=difficulty.value,
                uci_history=(),
                outlooks=(None,),
            )
            # Only an identifier collision can lose here, so a fresh one retries.
            if await self._store.compare_and_set(None, record):
                return GameState.from_store(record, chess.Board())

    async def reset_game(
        self,
        game_id: str,
        version: int,
        difficulty: Difficulty = Difficulty.CLUB,
    ) -> GameState:
        record = await self._get_record(game_id)
        self._require_version(record, version)
        replacement = replace(
            record,
            version=record.version + 1,
            difficulty=difficulty.value,
            uci_history=(),
            outlooks=(None,),
        )
        await self._commit(record, replacement)
        return GameState.from_store(replacement, chess.Board())

    async def get_game_state(self, game_id: str) -> GameState:
        record = await self._get_record(game_id)
        return GameState.from_store(record, self._board(record))

    async def play_white_move(
        self,
        game_id: str,
        version: int,
        move_uci: str,
    ) -> GameState:
        record = await self._get_record(game_id)
        self._require_version(record, version)
        board = self._board(record)
        if GameStatus.from_board(board) is not GameStatus.IN_PROGRESS:
            raise TerminalPositionError(board.fen(en_passant="fen"))
        if board.turn != chess.WHITE:
            raise NotPlayersTurnError()

        move = self._legal_move(board, move_uci)
        board.push(move)
        history = (*record.uci_history, move.uci())
        outlooks = (*record.outlooks, record.outlooks[-1])
        difficulty = Difficulty.from_store(record)

        if GameStatus.from_board(board) is GameStatus.IN_PROGRESS:
            evaluation = await self._analyze_move(board, difficulty)
            reply = self._choose(evaluation, difficulty)
            board.push(chess.Move.from_uci(reply.move_uci))
            history = (*history, reply.move_uci)
            outlooks = (*outlooks, reply.wdl.to_store())

        replacement = replace(
            record,
            version=record.version + 1,
            uci_history=history,
            outlooks=outlooks,
        )
        await self._commit(record, replacement)
        return GameState.from_store(replacement, board)

    async def analyze_position(self, game_id: str) -> PositionAnalysis:
        record = await self._get_record(game_id)
        board = self._board(record)
        if GameStatus.from_board(board) is not GameStatus.IN_PROGRESS:
            raise TerminalPositionError(board.fen(en_passant="fen"))
        evaluation = await self._analyze_position(board)
        return PositionAnalysis(
            game=GameState.from_store(record, board),
            candidates=evaluation.candidates,
        )

    async def undo_white_move(self, game_id: str, version: int) -> GameState:
        record = await self._get_record(game_id)
        self._require_version(record, version)
        if not record.uci_history:
            raise NothingToUndoError

        plies = 2 if len(record.uci_history) % 2 == 0 else 1
        replacement = replace(
            record,
            version=record.version + 1,
            uci_history=record.uci_history[:-plies],
            outlooks=record.outlooks[:-plies],
        )
        await self._commit(record, replacement)
        return GameState.from_store(replacement, self._board(replacement))

    @staticmethod
    def _new_game_id() -> str:
        return secrets.token_urlsafe(24)

    async def _get_record(self, game_id: str) -> StoredGameState:
        record = await self._store.get(game_id)
        if record is None:
            raise GameNotFoundError(game_id)
        return record

    async def _commit(
        self,
        expected: StoredGameState,
        replacement: StoredGameState,
    ) -> None:
        if await self._store.compare_and_set(expected, replacement):
            return
        # Another writer committed between the read and this write. Report the
        # conflict rather than retrying: the position moved, so the caller's
        # move may no longer be legal or even theirs to make.
        current = await self._store.get(replacement.game_id)
        if current is None:
            raise GameNotFoundError(replacement.game_id)
        raise GameVersionError(
            replacement.game_id,
            expected.version,
            current.version,
        )

    @staticmethod
    def _require_version(record: StoredGameState, version: int) -> None:
        if version != record.version:
            raise GameVersionError(record.game_id, version, record.version)

    @staticmethod
    def _board(record: StoredGameState) -> chess.Board:
        board = chess.Board()
        try:
            for move_uci in record.uci_history:
                board.push_uci(move_uci)
        except ValueError as error:
            raise StoreDataError(
                f"stored move history for game {record.game_id!r} is invalid"
            ) from error
        return board

    @staticmethod
    def _legal_move(board: chess.Board, move_uci: str) -> chess.Move:
        try:
            return board.parse_uci(move_uci)
        except ValueError as error:
            raise InvalidMoveError(move_uci) from error

    async def _analyze_position(self, board: chess.Board) -> Evaluation:
        infos = await self._engine.analyze(
            board.copy(stack=True),
            multipv=_ADVICE_CANDIDATES,
            nodes=_ANALYSIS_NODES,
        )
        return Evaluation.from_engine(board, infos)

    async def _analyze_move(
        self,
        board: chess.Board,
        difficulty: Difficulty,
    ) -> Evaluation:
        infos = await self._engine.analyze(
            board.copy(stack=True),
            multipv=_PRESETS[difficulty].candidates,
            nodes=_MOVE_NODES,
        )
        return Evaluation.from_engine(board, infos)

    @classmethod
    def _choose(cls, evaluation: Evaluation, difficulty: Difficulty) -> Move:
        preset = _PRESETS[difficulty]
        # Zero temperature is the argmax limit, and would divide by zero below.
        if preset.temperature <= 0:
            return evaluation.best

        # The best candidate always survives at loss 0, so this is never empty.
        viable = cls._viable_candidates(evaluation, preset.maximum_loss)
        moves = tuple(move for move, _ in viable)
        weights = tuple(math.exp(-loss / preset.temperature) for _, loss in viable)
        seed = f"{evaluation.fen}|{difficulty.value}"
        return random.Random(seed).choices(moves, weights=weights)[0]

    @staticmethod
    def _viable_candidates(
        evaluation: Evaluation,
        maximum_loss: float,
    ) -> tuple[tuple[Move, float], ...]:
        """Pair each candidate within ``maximum_loss`` of the best with that loss."""
        side = evaluation.side_to_move
        best = evaluation.best.wdl.expectation_for(side)
        scored = (
            (candidate, max(0.0, best - candidate.wdl.expectation_for(side)))
            for candidate in evaluation.candidates
        )
        return tuple((move, loss) for move, loss in scored if loss <= maximum_loss)
