from pathlib import Path

import httpx
import pytest
from fakes import RecordingEngine
from starlette.applications import Starlette

import mcp_app.main as main_module
from mcp_app.main import _create_app, _parse_args, main


def make_widget_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "dist"
    directory.mkdir()
    (directory / "index.html").write_text("<!doctype html><title>Built</title>")
    return directory


def make_executable(tmp_path: Path) -> Path:
    executable = tmp_path / "stockfish-test"
    executable.write_text("test")
    executable.chmod(0o755)
    return executable


async def call_tool(
    client: httpx.AsyncClient,
    name: str,
    arguments: dict[str, object],
) -> httpx.Response:
    return await client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )


def make_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    engine: RecordingEngine,
) -> Starlette:
    monkeypatch.setattr(main_module, "StockfishEngine", lambda _: engine)
    return _create_app(make_widget_dir(tmp_path))


def test_widget_argument_is_required_and_validated(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _parse_args([])
    with pytest.raises(SystemExit):
        _parse_args(["--widget_dir", str(tmp_path / "missing")])
    with pytest.raises(SystemExit):
        _parse_args(["--widget_dir", str(tmp_path)])


def test_parse_args_defaults(tmp_path: Path) -> None:
    directory = make_widget_dir(tmp_path)

    args = _parse_args(["--widget_dir", str(directory)])

    assert args.widget_dir == directory
    assert args.stockfish_path is None
    assert args.host == "127.0.0.1"
    assert args.port == 8000


def test_parse_args_accepts_widget_directory_alias(tmp_path: Path) -> None:
    directory = make_widget_dir(tmp_path)

    args = _parse_args(["--wdir", str(directory)])

    assert args.widget_dir == directory


def test_parse_args_accepts_runtime_overrides(tmp_path: Path) -> None:
    directory = make_widget_dir(tmp_path)
    executable = make_executable(tmp_path)

    args = _parse_args(
        [
            "--widget_dir",
            str(directory),
            "--stockfish_path",
            str(executable),
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
        ]
    )

    assert args.stockfish_path == executable.resolve()
    assert args.host == "0.0.0.0"
    assert args.port == 9000


@pytest.mark.parametrize("kind", ["missing", "directory", "non_executable"])
def test_stockfish_argument_rejects_unusable_targets(
    tmp_path: Path,
    kind: str,
) -> None:
    directory = make_widget_dir(tmp_path)
    if kind == "missing":
        target = tmp_path / "missing"
    elif kind == "directory":
        target = tmp_path
    else:
        target = tmp_path / "stockfish"
        target.write_text("test")
        target.chmod(0o644)

    with pytest.raises(SystemExit):
        _parse_args(
            [
                "--widget_dir",
                str(directory),
                "--stockfish_path",
                str(target),
            ]
        )


def test_create_app_constructs_engine_with_requested_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = RecordingEngine()
    executable = make_executable(tmp_path)
    calls: list[Path | None] = []

    def fake_engine(stockfish: Path | None) -> RecordingEngine:
        calls.append(stockfish)
        return engine

    monkeypatch.setattr(main_module, "StockfishEngine", fake_engine)

    app = _create_app(make_widget_dir(tmp_path), executable)

    assert isinstance(app, Starlette)
    assert calls == [executable]


@pytest.mark.asyncio
async def test_health_tracks_service_liveness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = RecordingEngine()
    app = make_app(monkeypatch, tmp_path, engine)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
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
async def test_one_service_lifespan_serves_every_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = RecordingEngine()
    app = make_app(monkeypatch, tmp_path, engine)

    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client,
        app.router.lifespan_context(app),
    ):
        assert engine.starts == 1
        started = await call_tool(client, "start_game", {})
        game_id = started.json()["result"]["structuredContent"]["game_id"]
        responses = [
            await call_tool(client, "get_game_state", {"game_id": game_id})
            for _ in range(3)
        ]
        health = await client.get("/health")

        assert all(response.status_code == 200 for response in responses)
        assert health.json() == {"status": "ok"}
        assert engine.starts == 1
        assert engine.stops == 0

    assert engine.starts == 1
    assert engine.stops == 1


def test_main_configures_and_runs_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {}
    executable = make_executable(tmp_path)
    widget_dir = make_widget_dir(tmp_path)
    app = object()

    def fake_create_app(directory: Path, stockfish: Path | None) -> object:
        calls.update(widget_dir=directory, stockfish=stockfish)
        return app

    def fake_run(asgi_app: object, *, host: str, port: int) -> None:
        calls.update(asgi_app=asgi_app, host=host, port=port)

    monkeypatch.setattr(main_module, "_create_app", fake_create_app)
    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)

    main(
        [
            "--widget_dir",
            str(widget_dir),
            "--stockfish_path",
            str(executable),
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
        ]
    )

    assert calls == {
        "widget_dir": widget_dir,
        "stockfish": executable.resolve(),
        "asgi_app": app,
        "host": "0.0.0.0",
        "port": 9000,
    }
