"""Analysis-engine adapters."""

from mcp_app.engine.protocol import Engine
from mcp_app.engine.stockfish import StockfishEngine

__all__ = ["Engine", "StockfishEngine"]
