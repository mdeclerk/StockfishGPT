from pathlib import Path

import httpx
import pytest
from fakes import FirstMoveEngine, RecordingEngine
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import LATEST_PROTOCOL_VERSION

from mcp_app.mcp.resources import (
    WIDGET_META,
    WIDGET_MIME_TYPE,
    WIDGET_URI,
    read_widget,
)
from mcp_app.mcp.server import SERVER_INSTRUCTIONS, McpServer
from mcp_app.service import ChessService
from mcp_app.store import GameStore, LocalGameStore


def make_widget_dir(
    tmp_path: Path,
    html: str = "<!doctype html><title>Built</title>",
) -> Path:
    directory = tmp_path / "dist"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "index.html").write_text(html)
    return directory


def make_mcp_server(
    tmp_path: Path,
    engine: FirstMoveEngine | None = None,
    *,
    store: GameStore | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> McpServer:
    return McpServer(
        ChessService(
            engine or FirstMoveEngine(),
            store if store is not None else LocalGameStore(),
        ),
        make_widget_dir(tmp_path),
        host=host,
        port=port,
    )


def make_server(
    tmp_path: Path,
    engine: FirstMoveEngine | None = None,
    *,
    store: GameStore | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP:
    return make_mcp_server(
        tmp_path,
        engine,
        store=store,
        host=host,
        port=port,
    ).fastmcp


def test_widget_resource_reads_bundle_and_reports_failure(tmp_path: Path) -> None:
    directory = make_widget_dir(tmp_path, "<!doctype html><title>Disk</title>")

    assert "Disk" in read_widget(directory)
    with pytest.raises(RuntimeError, match="could not be read"):
        read_widget(tmp_path / "missing")


def test_server_settings_and_instructions(tmp_path: Path) -> None:
    mcp = make_server(tmp_path)

    assert mcp.name == "StockfishGPT"
    assert mcp.settings.stateless_http is True
    assert mcp.settings.json_response is True
    assert mcp.settings.streamable_http_path == "/mcp"
    assert mcp.settings.host == "127.0.0.1"
    assert mcp.settings.port == 8000
    assert mcp.settings.transport_security is not None
    assert (
        mcp.settings.transport_security.enable_dns_rebinding_protection
        is False
    )
    assert mcp.instructions == SERVER_INSTRUCTIONS
    assert len(mcp.instructions) < 800
    assert "For any play/start request, immediately call `start_game`" in (
        mcp.instructions
    )
    assert "do not reply first" in mcp.instructions
    assert "Never claim a game started unless the call succeeded" in (
        mcp.instructions
    )
    assert "needs no board, FEN, or `game_id`" in mcp.instructions
    assert "Later, use the latest `game_id` and fresh state" in mcp.instructions
    assert "`get_game_state` for facts/status/history/rules" in mcp.instructions
    assert "`analyze_position` for advice/plans/tactics" in mcp.instructions
    assert "Never infer state or trust prior results" in mcp.instructions
    assert "widget state" in mcp.instructions
    assert "Evaluations use White's perspective" in mcp.instructions
    assert "advise the best move and Black's expected reply" in mcp.instructions
    assert "`start_game` is the model's only write" in mcp.instructions
    assert (
        "Only the board calls `reset_game`, `play_white_move`, or "
        "`undo_white_move`"
        in mcp.instructions
    )
    assert "direct those requests to its controls" in mcp.instructions


def test_server_accepts_network_overrides(tmp_path: Path) -> None:
    mcp = make_server(tmp_path, host="0.0.0.0", port=9000)

    assert mcp.settings.host == "0.0.0.0"
    assert mcp.settings.port == 9000
    assert mcp.settings.transport_security is not None
    assert (
        mcp.settings.transport_security.enable_dns_rebinding_protection
        is False
    )


@pytest.mark.asyncio
async def test_run_async_serves_streamable_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    server = make_mcp_server(tmp_path)
    runs = 0

    async def fake_run() -> None:
        nonlocal runs
        runs += 1

    assert isinstance(server.fastmcp, FastMCP)
    monkeypatch.setattr(server.fastmcp, "run_streamable_http_async", fake_run)

    await server.run_async()

    assert runs == 1


@pytest.mark.asyncio
async def test_health_tracks_service_liveness(tmp_path: Path) -> None:
    engine = RecordingEngine()
    mcp = make_server(tmp_path, engine)
    transport = httpx.ASGITransport(app=mcp.streamable_http_app())

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:8000",
    ) as client:
        stopped = await client.get("/health")
        await engine.start()
        started = await client.get("/health")

    assert stopped.status_code == 503
    assert stopped.json() == {"status": "engine_unavailable"}
    assert started.status_code == 200
    assert started.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_reports_store_unavailability(tmp_path: Path) -> None:
    class UnreadyStore(LocalGameStore):
        async def is_ready(self) -> bool:
            return False

    engine = RecordingEngine()
    mcp = make_server(tmp_path, engine, store=UnreadyStore())
    transport = httpx.ASGITransport(app=mcp.streamable_http_app())

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:8000",
    ) as client:
        await engine.start()
        response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "store_unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8000",
        "https://demo.trycloudflare.com",
        "https://stockfish-gpt-abc123.a.run.app",
    ],
)
async def test_server_speaks_streamable_http_at_mcp(
    tmp_path: Path,
    base_url: str,
) -> None:
    mcp = make_server(tmp_path)
    transport = httpx.ASGITransport(app=mcp.streamable_http_app())

    async with (
        mcp.session_manager.run(),
        httpx.AsyncClient(
            transport=transport,
            base_url=base_url,
        ) as client,
    ):
        initialized = await client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )

    assert initialized.status_code == 200
    assert initialized.json()["result"]["serverInfo"]["name"] == "StockfishGPT"


@pytest.mark.asyncio
async def test_tool_and_resource_contracts(tmp_path: Path) -> None:
    mcp = make_server(tmp_path)

    async with create_connected_server_and_client_session(mcp) as session:
        listed = await session.list_tools()
        resources = await session.list_resources()
        widget = await session.read_resource(WIDGET_URI)

    tools = {tool.name: tool for tool in listed.tools}
    assert set(tools) == {
        "start_game",
        "reset_game",
        "get_game_state",
        "play_white_move",
        "analyze_position",
        "undo_white_move",
    }
    assert WIDGET_URI == "ui://stockfish-gpt/v1/widget.html"
    assert tools["start_game"].meta == {
        "ui": {"resourceUri": WIDGET_URI, "visibility": ["model"]},
        "openai/outputTemplate": WIDGET_URI,
    }
    assert len(tools["start_game"].description) < 300
    assert "Immediately call for every play/start request" in tools[
        "start_game"
    ].description
    assert "do not reply first or claim success" in tools["start_game"].description
    assert "needs no board, FEN, or game_id" in tools["start_game"].description
    assert "Use once per chat" in tools["start_game"].description
    assert "board handles reset, moves, and undo" in tools["start_game"].description
    for name in ("reset_game", "play_white_move", "undo_white_move"):
        assert tools[name].meta == {"ui": {"visibility": ["app"]}}
    for name in ("get_game_state", "analyze_position"):
        assert tools[name].meta == {"ui": {"visibility": ["model", "app"]}}
    assert "fresh authoritative state" in tools["get_game_state"].description
    assert "fresh position" in tools["analyze_position"].description
    assert "authoritative snapshot" in tools["analyze_position"].description

    assert tools["start_game"].annotations.readOnlyHint is False
    assert tools["start_game"].annotations.destructiveHint is False
    for name in ("get_game_state", "analyze_position"):
        assert tools[name].annotations.readOnlyHint is True
        assert tools[name].annotations.idempotentHint is True
    assert tools["play_white_move"].annotations.destructiveHint is False
    for name in ("reset_game", "undo_white_move"):
        assert tools[name].annotations.destructiveHint is True

    game_properties = set(tools["start_game"].outputSchema["properties"])
    assert game_properties == {
        "game_id",
        "version",
        "difficulty",
        "fen",
        "side_to_move",
        "status",
        "is_in_check",
        "legal_moves",
        "uci_history",
        "san_history",
        "outlook",
        "ply_count",
    }
    assert set(tools["analyze_position"].outputSchema["properties"]) == {
        "game",
        "candidates",
    }
    assert set(tools["start_game"].inputSchema["properties"]) == {"difficulty"}
    assert set(tools["reset_game"].inputSchema["properties"]) == {
        "game_id",
        "version",
        "difficulty",
    }
    assert set(tools["get_game_state"].inputSchema["properties"]) == {"game_id"}
    assert set(tools["play_white_move"].inputSchema["properties"]) == {
        "game_id",
        "version",
        "move_uci",
    }
    assert set(tools["analyze_position"].inputSchema["properties"]) == {"game_id"}
    assert set(tools["undo_white_move"].inputSchema["properties"]) == {
        "game_id",
        "version",
    }

    assert len(resources.resources) == 1
    resource = resources.resources[0]
    assert str(resource.uri) == WIDGET_URI
    assert resource.mimeType == WIDGET_MIME_TYPE
    assert resource.meta == WIDGET_META
    assert widget.contents[0].mimeType == WIDGET_MIME_TYPE
    assert widget.contents[0].meta == WIDGET_META


@pytest.mark.asyncio
async def test_tools_share_authoritative_state_and_structured_responses(
    tmp_path: Path,
) -> None:
    mcp = make_server(tmp_path)

    async with create_connected_server_and_client_session(mcp) as session:
        started = await session.call_tool("start_game", {"difficulty": "strong"})
        game_id = started.structuredContent["game_id"]
        played = await session.call_tool(
            "play_white_move",
            {"game_id": game_id, "version": 0, "move_uci": "e2e4"},
        )
        current = await session.call_tool("get_game_state", {"game_id": game_id})
        analyzed = await session.call_tool("analyze_position", {"game_id": game_id})
        undone = await session.call_tool(
            "undo_white_move",
            {"game_id": game_id, "version": current.structuredContent["version"]},
        )
        reset = await session.call_tool(
            "reset_game",
            {
                "game_id": game_id,
                "version": undone.structuredContent["version"],
                "difficulty": "beginner",
            },
        )

    assert started.structuredContent["version"] == 0
    assert played.structuredContent["uci_history"][0] == "e2e4"
    assert current.structuredContent == played.structuredContent
    assert analyzed.structuredContent["game"] == current.structuredContent
    variation = analyzed.structuredContent["candidates"][0][
        "principal_variation"
    ]
    assert set(variation[0]) == {"move_uci", "move_san"}
    assert undone.structuredContent["ply_count"] == 0
    assert reset.structuredContent["game_id"] == game_id
    assert reset.structuredContent["difficulty"] == "beginner"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("get_game_state", {"game_id": "missing"}, "not found"),
        (
            "play_white_move",
            {"game_id": "missing", "version": 0, "move_uci": "e2e4"},
            "not found",
        ),
    ],
)
async def test_service_errors_propagate_as_tool_errors(
    tmp_path: Path,
    tool: str,
    arguments: dict[str, str | int],
    message: str,
) -> None:
    mcp = make_server(tmp_path)

    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool(tool, arguments)

    assert result.isError is True
    assert result.structuredContent is None
    assert message in result.content[0].text


def test_adapter_dependencies_do_not_cross(tmp_path: Path) -> None:
    package = Path(__file__).parents[1] / "src" / "mcp_app"
    mcp_sources = "\n".join(path.read_text() for path in (package / "mcp").glob("*.py"))
    engine_sources = "\n".join(
        path.read_text() for path in (package / "engine").glob("*.py")
    )
    service_sources = "\n".join(
        path.read_text() for path in (package / "service").glob("*.py")
    )
    store_sources = "\n".join(
        path.read_text() for path in (package / "store").glob("*.py")
    )

    assert "mcp_app.engine" not in mcp_sources
    assert "mcp_app.store" not in mcp_sources
    assert "mcp_app.mcp" not in engine_sources
    assert "mcp_app.service" not in engine_sources
    assert "mcp_app.store" not in engine_sources
    assert "mcp_app.mcp" not in service_sources
    assert "mcp_app.mcp" not in store_sources
    assert "mcp_app.service" not in store_sources
    assert "mcp_app.engine" not in store_sources
