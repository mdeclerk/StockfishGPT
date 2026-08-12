"""The operational health route."""

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_app.service import ChessService, ServiceStatus


def register_health_route(mcp: FastMCP, service: ChessService) -> None:
    """Register the liveness route."""

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_: Request) -> JSONResponse:
        status = await service.health_status()
        return JSONResponse(
            {"status": status.value},
            status_code=200 if status is ServiceStatus.OK else 503,
        )
