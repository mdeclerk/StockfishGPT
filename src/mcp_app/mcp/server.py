"""FastMCP server for StockfishGPT."""

from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mcp_app.service import ChessService

from .health import register_health_route
from .resources import register_widget_resource
from .tools import register_tools

SERVER_INSTRUCTIONS = Path(__file__).with_name("instructions.md").read_text().strip()


class McpServer:
    """Serve the StockfishGPT tool contract over Streamable HTTP at `/mcp`."""

    def __init__(
        self,
        service: ChessService,
        widget_dir: Path,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> None:
        self._fastmcp = FastMCP(
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
        register_health_route(self._fastmcp, service)
        register_widget_resource(self._fastmcp, widget_dir)
        register_tools(self._fastmcp, service)

    @property
    def fastmcp(self) -> FastMCP:
        """Return the underlying FastMCP server."""
        return self._fastmcp

    async def run_async(self) -> None:
        """Serve Streamable HTTP until the process is stopped."""
        await self._fastmcp.run_streamable_http_async()
