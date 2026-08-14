"""StockfishGPT CLI entry point."""

import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Self

from pydantic import Field, RedisDsn, ValidationError, field_validator
from pydantic_settings import BaseSettings, CliApp, SettingsConfigDict

from .engine import StockfishEngine
from .mcp import WIDGET_FILENAME, McpServer
from .service import ChessService
from .store import LocalGameStore, RedisGameStore


class Settings(BaseSettings):
    """Run the StockfishGPT MCP app."""

    widget_dir: Path = Field(
        default=Path("widget/dist"),
        description=(
            "Vite build output directory containing "
            f"{WIDGET_FILENAME} (for example `widget/dist`)"
        ),
    )
    stockfish_path: Path | None = Field(
        default=None,
        description="Path to the Stockfish executable (default: resolve from PATH)",
    )
    redis_url: RedisDsn | None = Field(
        default=None,
        description="Redis URL for shared game storage (default: local memory)",
    )
    host: str = Field(
        default="127.0.0.1",
        description="Host interface to bind",
    )
    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="Port to bind",
    )

    model_config = SettingsConfigDict(
        cli_kebab_case=True,
        cli_prog_name="mcp-app",
        cli_show_env_vars=True,
        env_file=None,
        extra="ignore",
    )

    @classmethod
    def from_args(cls, argv: Sequence[str] | None = None) -> Self:
        """Load and validate settings from CLI arguments and the environment."""
        cli_args = list(argv) if argv is not None else None
        try:
            return CliApp.run(cls, cli_args=cli_args)
        except ValidationError as error:
            print(f"mcp-app: configuration error:\n{error}", file=sys.stderr)
            raise SystemExit(2) from None

    @field_validator("widget_dir")
    @classmethod
    def validate_widget_directory(cls, directory: Path) -> Path:
        """Validate a widget build directory."""
        if not directory.is_dir():
            raise ValueError(f"{directory} is not a directory")
        if not (directory / WIDGET_FILENAME).is_file():
            raise ValueError(f"{directory} does not contain {WIDGET_FILENAME}")
        return directory

    @field_validator("stockfish_path")
    @classmethod
    def validate_stockfish_executable(cls, executable: Path | None) -> Path | None:
        """Validate an explicit Stockfish executable."""
        if executable is None:
            return None
        supplied_path = executable
        executable = executable.expanduser().resolve()
        if not executable.is_file():
            raise ValueError(f"{supplied_path} is not a file")
        if not os.access(executable, os.X_OK):
            raise ValueError(f"{supplied_path} is not executable")
        return executable


def main(argv: Sequence[str] | None = None) -> None:
    """Run Streamable HTTP at `/mcp`."""
    settings = Settings.from_args(argv)
    engine = StockfishEngine(settings.stockfish_path)
    store = (
        RedisGameStore.from_url(str(settings.redis_url))
        if settings.redis_url is not None
        else LocalGameStore()
    )
    service = ChessService(engine, store)
    server = McpServer(
        service,
        settings.widget_dir,
        host=settings.host,
        port=settings.port,
    )

    async def run_async() -> None:
        async with store, engine:
            await server.run_async()

    asyncio.run(run_async())
