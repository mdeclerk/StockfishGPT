"""Immutable chess-service domain models."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

import chess
import chess.engine

from mcp_app.store import StoreDataError, StoredGameState, StoredOutlook

from .errors import InvalidAnalysisError

_MAXIMUM_VARIATION_PLIES = 6


class Difficulty(StrEnum):
    BEGINNER = "beginner"
    CLUB = "club"
    STRONG = "strong"

    @classmethod
    def from_store(cls, record: StoredGameState) -> Self:
        """Convert a persisted difficulty, validating it in the process."""
        try:
            return cls(record.difficulty)
        except ValueError as error:
            raise StoreDataError(
                f"stored difficulty for game {record.game_id!r} is invalid"
            ) from error


class Side(StrEnum):
    """A chess side."""

    WHITE = "white"
    BLACK = "black"

    @classmethod
    def from_engine(cls, color: chess.Color) -> Self:
        """Convert a python-chess color."""
        return cls.WHITE if color == chess.WHITE else cls.BLACK


class GameStatus(StrEnum):
    """A complete game status understood by the widget."""

    IN_PROGRESS = "in_progress"
    WHITE_WINS_CHECKMATE = "white_wins_checkmate"
    BLACK_WINS_CHECKMATE = "black_wins_checkmate"
    DRAW_STALEMATE = "draw_stalemate"
    DRAW_INSUFFICIENT_MATERIAL = "draw_insufficient_material"
    DRAW_FIFTY_MOVE_RULE = "draw_fifty_move_rule"
    DRAW_THREEFOLD_REPETITION = "draw_threefold_repetition"

    @classmethod
    def from_board(cls, board: chess.Board) -> Self:
        """Derive the game status from a board position."""
        if board.is_checkmate():
            return (
                cls.BLACK_WINS_CHECKMATE
                if board.turn == chess.WHITE
                else cls.WHITE_WINS_CHECKMATE
            )
        if board.is_stalemate():
            return cls.DRAW_STALEMATE
        if board.is_insufficient_material():
            return cls.DRAW_INSUFFICIENT_MATERIAL
        if board.is_fifty_moves():
            return cls.DRAW_FIFTY_MOVE_RULE
        if board.is_repetition(3):
            return cls.DRAW_THREEFOLD_REPETITION
        return cls.IN_PROGRESS


class ServiceStatus(StrEnum):
    """Readiness of the service and its required dependencies."""

    OK = "ok"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    STORE_UNAVAILABLE = "store_unavailable"


@dataclass(frozen=True, slots=True)
class WinDrawLoss:
    """A White-perspective forecast."""

    white_wins: int
    draws: int
    black_wins: int

    def __post_init__(self) -> None:
        if min(self.white_wins, self.draws, self.black_wins) < 0:
            raise ValueError("W/D/L values must be non-negative")
        if self.total == 0:
            raise ValueError("W/D/L total must be positive")

    @classmethod
    def from_engine(
        cls,
        raw_wdl: Any,
        white_score: chess.engine.Score,
        ply: int,
    ) -> Self:
        """Convert Stockfish W/D/L or derive it from its score."""
        if isinstance(raw_wdl, chess.engine.PovWdl):
            wdl = raw_wdl.pov(chess.WHITE)
        elif isinstance(raw_wdl, chess.engine.Wdl):
            wdl = raw_wdl
        else:
            wdl = white_score.wdl(model="sf16.1", ply=ply)
        return cls(
            white_wins=wdl.wins,
            draws=wdl.draws,
            black_wins=wdl.losses,
        )

    @classmethod
    def from_store(cls, stored: StoredOutlook | None) -> Self | None:
        """Convert a persisted outlook, if one was recorded."""
        if stored is None:
            return None
        return cls(
            white_wins=stored.white_wins,
            draws=stored.draws,
            black_wins=stored.black_wins,
        )

    def to_store(self) -> StoredOutlook:
        """Convert to the persisted representation."""
        return StoredOutlook(
            white_wins=self.white_wins,
            draws=self.draws,
            black_wins=self.black_wins,
        )

    @property
    def total(self) -> int:
        return self.white_wins + self.draws + self.black_wins

    @property
    def white_expectation(self) -> float:
        return (self.white_wins + self.draws / 2) / self.total

    def expectation_for(self, side: Side) -> float:
        if side is Side.WHITE:
            return self.white_expectation
        return 1 - self.white_expectation


@dataclass(frozen=True, slots=True)
class VariationMove:
    """One move in a principal variation, in both wire notations."""

    move_uci: str
    move_san: str


@dataclass(frozen=True, slots=True)
class Move:
    """A move and its resulting evaluation."""

    move_uci: str
    move_san: str
    centipawns: int | None
    mate_in: int | None
    wdl: WinDrawLoss
    principal_variation: tuple[VariationMove, ...]

    @classmethod
    def from_engine(
        cls,
        board: chess.Board,
        info: chess.engine.InfoDict,
    ) -> Self:
        """Convert one Stockfish candidate."""
        variation = info.get("pv")
        score = info.get("score")
        if not variation or not isinstance(score, chess.engine.PovScore):
            raise InvalidAnalysisError(
                "candidate is missing a score or principal variation"
            )

        white_score = score.pov(chess.WHITE)
        return cls(
            move_uci=variation[0].uci(),
            move_san=board.san(variation[0]),
            centipawns=white_score.score(),
            mate_in=white_score.mate(),
            wdl=WinDrawLoss.from_engine(info.get("wdl"), white_score, board.ply()),
            principal_variation=cls._variation(board, variation),
        )

    @staticmethod
    def _variation(
        board: chess.Board,
        variation: list[chess.Move],
    ) -> tuple[VariationMove, ...]:
        replay = board.copy(stack=False)
        moves: list[VariationMove] = []
        for move in variation[:_MAXIMUM_VARIATION_PLIES]:
            if move not in replay.legal_moves:
                raise InvalidAnalysisError(
                    "principal variation contains an illegal move"
                )
            moves.append(VariationMove(move_uci=move.uci(), move_san=replay.san(move)))
            replay.push(move)
        return tuple(moves)


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Best-first engine candidates."""

    fen: str
    side_to_move: Side
    candidates: tuple[Move, ...]

    @classmethod
    def from_engine(
        cls,
        board: chess.Board,
        infos: list[chess.engine.InfoDict],
    ) -> Self:
        """Convert a complete Stockfish analysis."""
        candidates = tuple(Move.from_engine(board, info) for info in infos)
        if not candidates:
            raise InvalidAnalysisError("no candidate moves returned")
        return cls(
            fen=board.fen(en_passant="fen"),
            side_to_move=Side.from_engine(board.turn),
            candidates=candidates,
        )

    @property
    def best(self) -> Move:
        return self.candidates[0]


@dataclass(frozen=True, slots=True)
class GameState:
    """An authoritative snapshot of one server-owned game."""

    game_id: str
    version: int
    difficulty: Difficulty
    fen: str
    side_to_move: Side
    status: GameStatus
    is_in_check: bool
    legal_moves: tuple[str, ...]
    uci_history: tuple[str, ...]
    san_history: tuple[str, ...]
    outlook: WinDrawLoss | None

    @classmethod
    def from_store(cls, record: StoredGameState, board: chess.Board) -> Self:
        """Convert a stored record and its replayed board into a snapshot."""
        uci_history, san_history = cls._histories(board)
        return cls(
            game_id=record.game_id,
            version=record.version,
            difficulty=Difficulty.from_store(record),
            fen=board.fen(en_passant="fen"),
            side_to_move=Side.from_engine(board.turn),
            status=GameStatus.from_board(board),
            is_in_check=board.is_check(),
            legal_moves=tuple(move.uci() for move in board.legal_moves),
            uci_history=uci_history,
            san_history=san_history,
            outlook=WinDrawLoss.from_store(record.outlooks[-1]),
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

    @property
    def ply_count(self) -> int:
        return len(self.uci_history)


@dataclass(frozen=True, slots=True)
class PositionAnalysis:
    """Engine candidates bound to an authoritative game snapshot."""

    game: GameState
    candidates: tuple[Move, ...]
