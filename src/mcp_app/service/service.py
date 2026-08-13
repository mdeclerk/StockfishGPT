"""Store-backed chess games and Stockfish-backed behavior."""

import hashlib
import math
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import chess

from mcp_app.engine import Engine
from mcp_app.store import (
    GameLeaseLostError,
    GameLockedError,
    GameStore,
    StoreDataError,
    StoredGameState,
    StoredOutlook,
    locked,
)

from .errors import (
    GameBusyError,
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
    Side,
    WinDrawLoss,
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
        record = StoredGameState(
            game_id=self._new_game_id(),
            version=0,
            difficulty=difficulty.value,
            uci_history=(),
            outlooks=(None,),
        )
        async with self._gate(record.game_id) as fence:
            await self._store.set(record, fence)
        return self._snapshot(record, chess.Board())

    async def reset_game(
        self,
        game_id: str,
        version: int,
        difficulty: Difficulty = Difficulty.CLUB,
    ) -> GameState:
        async with self._gate(game_id) as fence:
            record = await self._get_record(game_id)
            self._require_version(record, version)
            replacement = StoredGameState(
                game_id=record.game_id,
                version=record.version + 1,
                difficulty=difficulty.value,
                uci_history=(),
                outlooks=(None,),
            )
            await self._commit(replacement, version, fence)
        return self._snapshot(replacement, chess.Board())

    async def get_game_state(self, game_id: str) -> GameState:
        record = await self._get_record(game_id)
        return self._snapshot(record, self._board(record))

    async def play_white_move(
        self,
        game_id: str,
        version: int,
        move_uci: str,
    ) -> GameState:
        async with self._gate(game_id) as fence:
            record = await self._get_record(game_id)
            self._require_version(record, version)
            board = self._board(record)
            if self._status(board) is not GameStatus.IN_PROGRESS:
                raise TerminalPositionError(board.fen(en_passant="fen"))
            if board.turn != chess.WHITE:
                raise NotPlayersTurnError()

            move = self._legal_move(board, move_uci)
            board.push(move)
            history = (*record.uci_history, move.uci())
            outlooks = (*record.outlooks, record.outlooks[-1])
            difficulty = self._difficulty(record)

            if self._status(board) is GameStatus.IN_PROGRESS:
                evaluation = await self._analyze_move(board, difficulty)
                reply = self._choose(evaluation, difficulty)
                board.push(chess.Move.from_uci(reply.move_uci))
                history = (*history, reply.move_uci)
                outlooks = (*outlooks, self._store_outlook(reply.wdl))

            replacement = StoredGameState(
                game_id=record.game_id,
                version=record.version + 1,
                difficulty=record.difficulty,
                uci_history=history,
                outlooks=outlooks,
            )
            await self._commit(replacement, version, fence)
        return self._snapshot(replacement, board)

    async def analyze_position(self, game_id: str) -> PositionAnalysis:
        async with self._gate(game_id):
            record = await self._get_record(game_id)
            board = self._board(record)
            if self._status(board) is not GameStatus.IN_PROGRESS:
                raise TerminalPositionError(board.fen(en_passant="fen"))
            evaluation = await self._analyze_position(board)
        return PositionAnalysis(
            game=self._snapshot(record, board),
            candidates=evaluation.candidates,
        )

    async def undo_white_move(self, game_id: str, version: int) -> GameState:
        async with self._gate(game_id) as fence:
            record = await self._get_record(game_id)
            self._require_version(record, version)
            if not record.uci_history:
                raise NothingToUndoError

            plies = 2 if len(record.uci_history) % 2 == 0 else 1
            replacement = StoredGameState(
                game_id=record.game_id,
                version=record.version + 1,
                difficulty=record.difficulty,
                uci_history=record.uci_history[:-plies],
                outlooks=record.outlooks[:-plies],
            )
            await self._commit(replacement, version, fence)
        return self._snapshot(replacement, self._board(replacement))

    @staticmethod
    def _new_game_id() -> str:
        return secrets.token_urlsafe(24)

    @asynccontextmanager
    async def _gate(self, game_id: str) -> AsyncIterator[str]:
        try:
            async with locked(self._store, game_id) as fence:
                yield fence
        except GameLockedError as error:
            raise GameBusyError(game_id) from error

    async def _get_record(self, game_id: str) -> StoredGameState:
        record = await self._store.get(game_id)
        if record is None:
            raise GameNotFoundError(game_id)
        return record

    async def _commit(
        self,
        replacement: StoredGameState,
        requested_version: int,
        fence: str,
    ) -> None:
        try:
            await self._store.set(replacement, fence)
        except GameLeaseLostError as error:
            # A successor may have taken over after our lock lease expired.
            current = await self._store.get(replacement.game_id)
            if current is None:
                raise GameNotFoundError(replacement.game_id) from error
            raise GameVersionError(
                replacement.game_id,
                requested_version,
                current.version,
            ) from error

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
    def _difficulty(record: StoredGameState) -> Difficulty:
        try:
            return Difficulty(record.difficulty)
        except ValueError as error:
            raise StoreDataError(
                f"stored difficulty for game {record.game_id!r} is invalid"
            ) from error

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
        return Evaluation.from_stockfish(board, infos)

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
        return Evaluation.from_stockfish(board, infos)

    @classmethod
    def _snapshot(cls, record: StoredGameState, board: chess.Board) -> GameState:
        uci_history, san_history = cls._histories(board)
        return GameState(
            game_id=record.game_id,
            version=record.version,
            difficulty=cls._difficulty(record),
            fen=board.fen(en_passant="fen"),
            side_to_move=Side.from_stockfish(board.turn),
            status=cls._status(board),
            is_in_check=board.is_check(),
            legal_moves=tuple(move.uci() for move in board.legal_moves),
            uci_history=uci_history,
            san_history=san_history,
            outlook=cls._service_outlook(record.outlooks[-1]),
        )

    @staticmethod
    def _store_outlook(outlook: WinDrawLoss) -> StoredOutlook:
        return StoredOutlook(
            white_wins=outlook.white_wins,
            draws=outlook.draws,
            black_wins=outlook.black_wins,
        )

    @staticmethod
    def _service_outlook(outlook: StoredOutlook | None) -> WinDrawLoss | None:
        if outlook is None:
            return None
        return WinDrawLoss(
            white_wins=outlook.white_wins,
            draws=outlook.draws,
            black_wins=outlook.black_wins,
        )

    @staticmethod
    def _histories(board: chess.Board) -> tuple[tuple[str, ...], tuple[str, ...]]:
        replay = board.root()
        uci_history: list[str] = []
        san_history: list[str] = []
        for move in board.move_stack:
            uci_history.append(move.uci())
            san_history.append(replay.san_and_push(move))
        return tuple(uci_history), tuple(san_history)

    @staticmethod
    def _status(board: chess.Board) -> GameStatus:
        if board.is_checkmate():
            return (
                GameStatus.BLACK_WINS_CHECKMATE
                if board.turn == chess.WHITE
                else GameStatus.WHITE_WINS_CHECKMATE
            )
        if board.is_stalemate():
            return GameStatus.DRAW_STALEMATE
        if board.is_insufficient_material():
            return GameStatus.DRAW_INSUFFICIENT_MATERIAL
        if board.is_fifty_moves():
            return GameStatus.DRAW_FIFTY_MOVE_RULE
        if board.is_repetition(3):
            return GameStatus.DRAW_THREEFOLD_REPETITION
        return GameStatus.IN_PROGRESS

    @classmethod
    def _choose(cls, evaluation: Evaluation, difficulty: Difficulty) -> Move:
        if difficulty is Difficulty.STRONG:
            return evaluation.best

        preset = _PRESETS[difficulty]
        viable, losses = cls._viable_candidates(evaluation, preset.maximum_loss)
        weights = tuple(math.exp(-loss / preset.temperature) for loss in losses)
        seed = f"{evaluation.fen}|{difficulty.value}"
        return cls._weighted_pick(viable, weights, seed)

    @staticmethod
    def _viable_candidates(
        evaluation: Evaluation,
        maximum_loss: float,
    ) -> tuple[tuple[Move, ...], tuple[float, ...]]:
        side = evaluation.side_to_move
        best_expectation = evaluation.best.wdl.expectation_for(side)
        candidates: list[Move] = []
        losses: list[float] = []
        for candidate in evaluation.candidates:
            expectation = candidate.wdl.expectation_for(side)
            loss = max(0.0, best_expectation - expectation)
            if loss <= maximum_loss:
                candidates.append(candidate)
                losses.append(loss)
        return tuple(candidates), tuple(losses)

    @staticmethod
    def _weighted_pick(
        candidates: tuple[Move, ...],
        weights: tuple[float, ...],
        seed: str,
    ) -> Move:
        total = sum(weights)
        digest = hashlib.sha256(seed.encode()).digest()
        point = int.from_bytes(digest[:8]) / 2**64 * total
        cumulative = 0.0
        for candidate, weight in zip(candidates, weights, strict=True):
            cumulative += weight
            if point < cumulative:
                return candidate
        return candidates[-1]
