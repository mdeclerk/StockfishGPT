"""Game-store contracts and adapters."""

from .errors import StoreDataError, StoreError, StoreUnavailableError
from .local import LocalGameStore
from .models import GameRecord, StoredOutlook
from .protocol import GameStore
from .redis import RedisGameStore

__all__ = [
    "GameRecord",
    "GameStore",
    "LocalGameStore",
    "RedisGameStore",
    "StoreDataError",
    "StoreError",
    "StoreUnavailableError",
    "StoredOutlook",
]
