"""Immutable persistence records shared by game-store adapters."""

from typing import Annotated

from pydantic import Field, StringConstraints
from pydantic.dataclasses import dataclass

_NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
_NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]


@dataclass(frozen=True, slots=True)
class StoredOutlook:
    """A persisted White-perspective win/draw/loss forecast."""

    white_wins: _NonNegativeInt
    draws: _NonNegativeInt
    black_wins: _NonNegativeInt

    def __post_init__(self) -> None:
        if self.white_wins + self.draws + self.black_wins == 0:
            raise ValueError("stored outlook total must be positive")


@dataclass(frozen=True, slots=True)
class StoredGameState:
    """Minimal authoritative state needed to reconstruct one game."""

    game_id: _NonEmptyStr
    version: _NonNegativeInt
    difficulty: _NonEmptyStr
    uci_history: tuple[_NonEmptyStr, ...]
    outlooks: tuple[StoredOutlook | None, ...]

    def __post_init__(self) -> None:
        if len(self.outlooks) != len(self.uci_history) + 1:
            raise ValueError("outlooks must contain one entry per ply plus the root")
