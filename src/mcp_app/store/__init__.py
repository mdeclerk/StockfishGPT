"""Game-store contracts and adapters."""

from .errors import (
    GameLeaseLostError,
    GameLockedError,
    StoreDataError,
    StoreError,
    StoreUnavailableError,
)
from .local import LocalGameStore
from .models import StoredGameState, StoredOutlook
from .protocol import GameStore, locked
from .redis import RedisGameStore

__all__ = [
    "GameLeaseLostError",
    "GameLockedError",
    "StoredGameState",
    "GameStore",
    "LocalGameStore",
    "RedisGameStore",
    "StoreDataError",
    "StoreError",
    "StoreUnavailableError",
    "StoredOutlook",
    "locked",
]
