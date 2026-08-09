"""StockfishGPT CLI entry point."""

import argparse
import asyncio
import os
from collections.abc import Sequence
from pathlib import Path

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


def main(argv: Sequence[str] | None = None) -> None:
    """Run Streamable HTTP at `/mcp`."""
    args = _parse_args(argv)
    engine = StockfishEngine(args.stockfish_path)
    service = ChessService(engine)
    mcp = create_server(
        service,
        args.widget_dir,
        host=args.host,
        port=args.port,
    )

    async def run_async() -> None:
        await service.start()
        try:
            await mcp.run_streamable_http_async()
        finally:
            await service.close()

    asyncio.run(run_async())
