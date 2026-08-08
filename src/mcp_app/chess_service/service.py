"""Server-owned chess games and Stockfish-backed behavior."""

import asyncio
import hashlib
import math
import secrets
from dataclasses import dataclass, field
from time import monotonic
from typing import Protocol, runtime_checkable

import chess
import chess.engine

from mcp_app.chess_service.errors import (
    GameNotFoundError,
    GameVersionError,
    InvalidMoveError,
    NothingToUndoError,
    NotPlayersTurnError,
    TerminalPositionError,
)
from mcp_app.chess_service.models import (
    Difficulty,
    Evaluation,
    GameState,
    GameStatus,
    Move,
    PositionAnalysis,
    Side,
    WinDrawLoss,
)

_ADVICE_CANDIDATES = 3
_ANALYSIS_NODES = 60_000
_MOVE_NODES = 80_000
_GAME_TTL_SECONDS = 3600.0
_MAX_GAMES = 1024


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


@runtime_checkable
class _Engine(Protocol):
    """Raw engine analysis and lifecycle."""

    @property
    def is_alive(self) -> bool: ...

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def analyze(
        self,
        board: chess.Board,
        *,
        multipv: int,
        nodes: int,
    ) -> list[chess.engine.InfoDict]: ...


@dataclass(slots=True)
class _Game:
    game_id: str
    difficulty: Difficulty
    board: chess.Board = field(default_factory=chess.Board)
    version: int = 0
    outlooks: list[WinDrawLoss | None] = field(default_factory=lambda: [None])
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_access: float = field(default_factory=monotonic)


class ChessService:
    """Own games and run their engine-backed use cases."""

    def __init__(self, engine: _Engine) -> None:
        self._engine = engine
        self._games: dict[str, _Game] = {}

    @property
    def is_alive(self) -> bool:
        return self._engine.is_alive

    async def start(self) -> None:
        await self._engine.start()

    async def close(self) -> None:
        await self._engine.close()

    async def start_game(
        self,
        difficulty: Difficulty = Difficulty.CLUB,
    ) -> GameState:
        self._sweep_expired()
        if len(self._games) >= _MAX_GAMES:
            oldest_id = min(
                self._games, key=lambda game_id: self._games[game_id].last_access
            )
            del self._games[oldest_id]
        game_id = self._new_game_id()
        game = _Game(game_id=game_id, difficulty=difficulty)
        self._games[game_id] = game
        return self._snapshot(game)

    async def reset_game(
        self,
        game_id: str,
        version: int,
        difficulty: Difficulty = Difficulty.CLUB,
    ) -> GameState:
        game = self._get_game(game_id)
        async with game.lock:
            self._require_version(game, version)
            game.board = chess.Board()
            game.difficulty = difficulty
            game.outlooks = [None]
            game.version += 1
            return self._snapshot(game)

    async def get_game_state(self, game_id: str) -> GameState:
        game = self._get_game(game_id)
        async with game.lock:
            return self._snapshot(game)

    async def play_white_move(
        self,
        game_id: str,
        version: int,
        move_uci: str,
    ) -> GameState:
        game = self._get_game(game_id)
        async with game.lock:
            self._require_version(game, version)
            if self._status(game.board) is not GameStatus.IN_PROGRESS:
                raise TerminalPositionError(game.board.fen(en_passant="fen"))
            if game.board.turn != chess.WHITE:
                raise NotPlayersTurnError()

            board = game.board.copy(stack=True)
            move = self._legal_move(board, move_uci)
            board.push(move)
            outlooks = [*game.outlooks, game.outlooks[-1]]

            if self._status(board) is GameStatus.IN_PROGRESS:
                evaluation = await self._analyze_move(board, game.difficulty)
                reply = self._choose(evaluation, game.difficulty)
                board.push(chess.Move.from_uci(reply.move_uci))
                outlooks.append(reply.wdl)

            game.board = board
            game.outlooks = outlooks
            game.version += 1
            return self._snapshot(game)

    async def analyze_position(self, game_id: str) -> PositionAnalysis:
        game = self._get_game(game_id)
        async with game.lock:
            if self._status(game.board) is not GameStatus.IN_PROGRESS:
                raise TerminalPositionError(game.board.fen(en_passant="fen"))
            evaluation = await self._analyze_position(game.board)
            return PositionAnalysis(
                game=self._snapshot(game),
                candidates=evaluation.candidates,
            )

    async def undo_white_move(self, game_id: str, version: int) -> GameState:
        game = self._get_game(game_id)
        async with game.lock:
            self._require_version(game, version)
            if not game.board.move_stack:
                raise NothingToUndoError

            plies = 2 if len(game.board.move_stack) % 2 == 0 else 1
            for _ in range(plies):
                game.board.pop()
                game.outlooks.pop()
            game.version += 1
            return self._snapshot(game)

    def _new_game_id(self) -> str:
        while True:
            game_id = secrets.token_urlsafe(24)
            if game_id not in self._games:
                return game_id

    def _get_game(self, game_id: str) -> _Game:
        try:
            game = self._games[game_id]
        except KeyError as error:
            raise GameNotFoundError(game_id) from error
        now = monotonic()
        if now - game.last_access > _GAME_TTL_SECONDS:
            del self._games[game_id]
            raise GameNotFoundError(game_id)
        game.last_access = now
        return game

    def _sweep_expired(self) -> None:
        now = monotonic()
        expired = [
            game_id
            for game_id, game in self._games.items()
            if now - game.last_access > _GAME_TTL_SECONDS
        ]
        for game_id in expired:
            del self._games[game_id]

    @staticmethod
    def _require_version(game: _Game, version: int) -> None:
        if version != game.version:
            raise GameVersionError(game.game_id, version, game.version)

    @staticmethod
    def _legal_move(board: chess.Board, move_uci: str) -> chess.Move:
        try:
            move = chess.Move.from_uci(move_uci)
        except ValueError as error:
            raise InvalidMoveError(move_uci) from error
        if move not in board.legal_moves:
            raise InvalidMoveError(move_uci)
        return move

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
    def _snapshot(cls, game: _Game) -> GameState:
        uci_history, san_history = cls._histories(game.board)
        return GameState(
            game_id=game.game_id,
            version=game.version,
            difficulty=game.difficulty,
            fen=game.board.fen(en_passant="fen"),
            side_to_move=Side.from_stockfish(game.board.turn),
            status=cls._status(game.board),
            is_in_check=game.board.is_check(),
            legal_moves=tuple(move.uci() for move in game.board.legal_moves),
            uci_history=uci_history,
            san_history=san_history,
            outlook=game.outlooks[-1],
        )

    @staticmethod
    def _histories(board: chess.Board) -> tuple[tuple[str, ...], tuple[str, ...]]:
        replay = board.root()
        uci_history: list[str] = []
        san_history: list[str] = []
        for move in board.move_stack:
            uci_history.append(move.uci())
            san_history.append(replay.san(move))
            replay.push(move)
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
