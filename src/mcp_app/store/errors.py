"""Typed game-store failures."""


class StoreError(RuntimeError):
    """Base class for game-store failures."""


class StoreDataError(StoreError):
    """Stored game data is malformed or internally inconsistent."""


class StoreUnavailableError(StoreError):
    """The configured game store cannot currently be reached."""
