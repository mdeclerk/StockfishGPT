"""Game-store contracts and adapters."""

from .errors import (
    GameLeaseLostError,
    GameLockedError,
    StoreDataError,
    StoreError,
    StoreUnavailableError,
)
from .local import DEFAULT_GAME_TTL_SECONDS, LocalGameStore
from .models import StoredGameState, StoredOutlook
from .protocol import GameStore, locked
from .redis import DEFAULT_LOCK_TTL_SECONDS, RedisGameStore

__all__ = [
    "DEFAULT_GAME_TTL_SECONDS",
    "DEFAULT_LOCK_TTL_SECONDS",
    "GameLeaseLostError",
    "GameLockedError",
    "GameStore",
    "LocalGameStore",
    "RedisGameStore",
    "StoreDataError",
    "StoreError",
    "StoreUnavailableError",
    "StoredGameState",
    "StoredOutlook",
    "locked",
]
