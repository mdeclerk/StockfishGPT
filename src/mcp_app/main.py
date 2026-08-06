"""StockfishGPT CLI entry point."""

import argparse
import os
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_app.chess_service import ChessService
from mcp_app.mcp import WIDGET_FILENAME, create_server
from mcp_app.stockfish import StockfishEngine

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _widget_directory(value: str) -> Path:
    """Validate a widget build directory."""
    directory = Path(value)
    if not directory.is_dir():
        raise argparse.ArgumentTypeError(f"{value} is not a directory")
    if not (directory / WIDGET_FILENAME).is_file():
        raise argparse.ArgumentTypeError(f"{value} does not contain {WIDGET_FILENAME}")
    return directory


def _stockfish_executable(value: str) -> Path:
    """Validate an explicit Stockfish executable."""
    executable = Path(value).expanduser().resolve()
    if not executable.is_file():
        raise argparse.ArgumentTypeError(f"{value} is not a file")
    if not os.access(executable, os.X_OK):
        raise argparse.ArgumentTypeError(f"{value} is not executable")
    return executable


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="mcp-app",
        description="Run the StockfishGPT MCP app.",
    )
    parser.add_argument(
        "--widget_dir",
        "--wdir",
        dest="widget_dir",
        required=True,
        type=_widget_directory,
        metavar="DIR",
        help=(
            "Vite build output directory containing "
            f"{WIDGET_FILENAME} (for example `widget/dist`)"
        ),
    )
    parser.add_argument(
        "--stockfish_path",
        type=_stockfish_executable,
        metavar="FILE",
        help="Path to the Stockfish executable (default: resolve from PATH)",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Host interface to bind (default: %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Port to bind (default: %(default)s)",
    )
    return parser.parse_args(argv)


def _create_app(widget_dir: Path, stockfish_path: Path | None = None) -> Starlette:
    """Create the StockfishGPT ASGI application and its dependencies."""
    engine = StockfishEngine(stockfish_path)
    service = ChessService(engine)
    mcp = create_server(service, widget_dir)
    app = mcp.streamable_http_app()

    async def health(_: Request) -> JSONResponse:
        if not service.is_alive:
            return JSONResponse({"status": "engine_unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    app.router.add_route(
        "/health", health, methods=["GET"], include_in_schema=False
    )
    sessions = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(scoped: Starlette) -> AsyncGenerator[None]:
        await service.start()
        try:
            async with sessions(scoped):
                yield
        finally:
            await service.close()

    app.router.lifespan_context = lifespan
    return app


def main(argv: Sequence[str] | None = None) -> None:
    """Run Streamable HTTP at `/mcp`."""
    args = _parse_args(argv)
    app = _create_app(args.widget_dir, args.stockfish_path)
    uvicorn.run(app, host=args.host, port=args.port)
