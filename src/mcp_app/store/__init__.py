"""Game-store contracts and adapters."""

from .errors import StoreDataError, StoreError, StoreUnavailableError
from .local import DEFAULT_GAME_TTL_SECONDS, LocalGameStore
from .models import StoredGameState, StoredOutlook
from .protocol import GameStore
from .redis import RedisGameStore

__all__ = [
    "DEFAULT_GAME_TTL_SECONDS",
    "GameStore",
    "LocalGameStore",
    "RedisGameStore",
    "StoreDataError",
    "StoreError",
    "StoreUnavailableError",
    "StoredGameState",
    "StoredOutlook",
]
