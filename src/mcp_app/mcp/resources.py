"""The MCP widget resource."""

from pathlib import Path

from mcp.server.fastmcp import FastMCP

WIDGET_FILENAME = "index.html"
WIDGET_URI = "ui://stockfish-gpt/v1/widget.html"
WIDGET_MIME_TYPE = "text/html;profile=mcp-app"
WIDGET_META = {
    "ui": {
        "csp": {"connectDomains": [], "resourceDomains": []},
        "prefersBorder": False,
    },
    "openai/widgetDescription": (
        "Interactive board: user plays White with engine analysis."
    ),
}


def read_widget(widget_dir: Path) -> str:
    """Read the built widget bundle from disk."""
    bundle = widget_dir / WIDGET_FILENAME
    try:
        return bundle.read_text()
    except OSError as error:
        raise RuntimeError(f"Widget bundle {bundle} could not be read.") from error


def register_widget_resource(mcp: FastMCP, widget_dir: Path) -> None:
    """Register the widget resource."""

    @mcp.resource(
        WIDGET_URI,
        name="StockfishGPT",
        description="Interactive board for White.",
        mime_type=WIDGET_MIME_TYPE,
        meta=WIDGET_META,
    )
    def widget() -> str:
        return read_widget(widget_dir)
