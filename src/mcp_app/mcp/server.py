"""FastMCP construction for StockfishGPT."""

from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_app.mcp.resources import register_widget_resource
from mcp_app.mcp.tools import register_tools
from mcp_app.service import ChessService

SERVER_INSTRUCTIONS = Path(__file__).with_name("instructions.md").read_text().strip()


def create_server(
    service: ChessService,
    widget_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP:
    """Create the configured FastMCP server."""
    mcp = FastMCP(
        "StockfishGPT",
        instructions=SERVER_INSTRUCTIONS,
        host=host,
        port=port,
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_: Request) -> JSONResponse:
        if not service.is_alive:
            return JSONResponse({"status": "engine_unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    register_tools(mcp, service)
    register_widget_resource(mcp, widget_dir)
    return mcp
