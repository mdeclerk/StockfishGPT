"""Analysis-engine contracts and adapters."""

from .errors import (
    EngineBrokenError,
    EngineError,
    EngineNotFoundError,
    EngineNotStartedError,
    EngineProcessError,
    EngineRestartingError,
    EngineTimeoutError,
    EngineUnavailableError,
)
from .protocol import Engine
from .stockfish import StockfishEngine

__all__ = [
    "Engine",
    "EngineBrokenError",
    "EngineError",
    "EngineNotFoundError",
    "EngineNotStartedError",
    "EngineProcessError",
    "EngineRestartingError",
    "EngineTimeoutError",
    "EngineUnavailableError",
    "StockfishEngine",
]
