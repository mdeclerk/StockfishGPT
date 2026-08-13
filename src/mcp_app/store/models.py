"""Immutable persistence records shared by game-store adapters."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StoredOutlook:
    """A persisted White-perspective win/draw/loss forecast."""

    white_wins: int
    draws: int
    black_wins: int

    def __post_init__(self) -> None:
        values = (self.white_wins, self.draws, self.black_wins)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in values
        ):
            raise ValueError("stored outlook values must be non-negative integers")
        if sum(values) == 0:
            raise ValueError("stored outlook total must be positive")


@dataclass(frozen=True, slots=True)
class StoredGameState:
    """Minimal authoritative state needed to reconstruct one game."""

    game_id: str
    version: int
    difficulty: str
    uci_history: tuple[str, ...]
    outlooks: tuple[StoredOutlook | None, ...]

    def __post_init__(self) -> None:
        if not self.game_id:
            raise ValueError("game_id must not be empty")
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version < 0
        ):
            raise ValueError("version must be a non-negative integer")
        if not self.difficulty:
            raise ValueError("difficulty must not be empty")
        if any(not move for move in self.uci_history):
            raise ValueError("UCI moves must not be empty")
        if len(self.outlooks) != len(self.uci_history) + 1:
            raise ValueError("outlooks must contain one entry per ply plus the root")
