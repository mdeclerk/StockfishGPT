"""MCP wire response schemas."""

from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    NonNegativeInt,
    computed_field,
    model_validator,
)

from mcp_app.chess_service import (
    Difficulty,
    GameState,
    GameStatus,
    Move,
    PositionAnalysis,
    Side,
    VariationMove,
    WinDrawLoss,
)


class BaseSchema(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        json_schema_mode_override="serialization",
    )


class WinDrawLossSchema(BaseSchema):
    """A White-perspective forecast."""

    white_wins: NonNegativeInt
    draws: NonNegativeInt
    black_wins: NonNegativeInt

    @model_validator(mode="after")
    def _require_a_positive_total(self) -> Self:
        if self.total == 0:
            raise ValueError("W/D/L total must be positive")
        return self

    @classmethod
    def from_domain(cls, wdl: WinDrawLoss) -> Self:
        return cls(
            white_wins=wdl.white_wins,
            draws=wdl.draws,
            black_wins=wdl.black_wins,
        )

    @property
    def total(self) -> int:
        return self.white_wins + self.draws + self.black_wins

    @computed_field  # type: ignore[prop-decorator]
    @property
    def white_expectation(self) -> float:
        return (self.white_wins + self.draws / 2) / self.total


class VariationMoveSchema(BaseSchema):
    """One move in a principal variation."""

    move_uci: str
    move_san: str

    @classmethod
    def from_domain(cls, move: VariationMove) -> Self:
        return cls(move_uci=move.move_uci, move_san=move.move_san)


class MoveSchema(BaseSchema):
    """A move and its resulting evaluation."""

    move_uci: str
    move_san: str
    centipawns: int | None
    mate_in: int | None
    wdl: WinDrawLossSchema
    principal_variation: tuple[VariationMoveSchema, ...]

    @classmethod
    def from_domain(cls, move: Move) -> Self:
        return cls(
            move_uci=move.move_uci,
            move_san=move.move_san,
            centipawns=move.centipawns,
            mate_in=move.mate_in,
            wdl=WinDrawLossSchema.from_domain(move.wdl),
            principal_variation=tuple(
                VariationMoveSchema.from_domain(item)
                for item in move.principal_variation
            ),
        )


class GameStateSchema(BaseSchema):
    """An authoritative game snapshot."""

    game_id: str
    version: NonNegativeInt
    difficulty: Difficulty
    fen: str
    side_to_move: Side
    status: GameStatus
    is_in_check: bool
    legal_moves: tuple[str, ...]
    uci_history: tuple[str, ...]
    san_history: tuple[str, ...]
    outlook: WinDrawLossSchema | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ply_count(self) -> int:
        return len(self.uci_history)

    @classmethod
    def from_domain(cls, state: GameState) -> Self:
        return cls(
            game_id=state.game_id,
            version=state.version,
            difficulty=state.difficulty,
            fen=state.fen,
            side_to_move=state.side_to_move,
            status=state.status,
            is_in_check=state.is_in_check,
            legal_moves=state.legal_moves,
            uci_history=state.uci_history,
            san_history=state.san_history,
            outlook=(
                WinDrawLossSchema.from_domain(state.outlook)
                if state.outlook
                else None
            ),
        )


class PositionAnalysisSchema(BaseSchema):
    """Best-first candidates for one authoritative game snapshot."""

    game: GameStateSchema
    candidates: tuple[MoveSchema, ...]

    @classmethod
    def from_domain(cls, analysis: PositionAnalysis) -> Self:
        return cls(
            game=GameStateSchema.from_domain(analysis.game),
            candidates=tuple(
                MoveSchema.from_domain(move) for move in analysis.candidates
            ),
        )
