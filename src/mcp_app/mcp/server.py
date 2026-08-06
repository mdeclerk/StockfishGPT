"""FastMCP construction for StockfishGPT."""

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_app.chess_service import ChessService
from mcp_app.mcp.resources import register_widget_resource
from mcp_app.mcp.tools import register_tools

SERVER_INSTRUCTIONS = Path(__file__).with_name("instructions.md").read_text().strip()


def create_server(
    service: ChessService,
    widget_dir: Path,
) -> FastMCP:
    """Create the configured FastMCP server."""
    mcp = FastMCP(
        "StockfishGPT",
        instructions=SERVER_INSTRUCTIONS,
        json_response=True,
        stateless_http=True,
    )
    register_tools(mcp, service)
    register_widget_resource(mcp, widget_dir)
    return mcp
