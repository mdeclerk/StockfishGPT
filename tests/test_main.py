from pathlib import Path

import pytest
from fakes import RecordingEngine

import mcp_app.main as main_module
from mcp_app.main import _parse_args, main


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


def test_main_configures_and_runs_server_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {}
    engine = RecordingEngine()
    executable = make_executable(tmp_path)
    widget_dir = make_widget_dir(tmp_path)

    class FakeServer:
        async def run_streamable_http_async(self) -> None:
            calls["runs"] = int(calls.get("runs", 0)) + 1
            assert engine.running is True

    def fake_engine(stockfish: Path | None) -> RecordingEngine:
        calls["stockfish"] = stockfish
        return engine

    def fake_create_server(
        service: object,
        directory: Path,
        *,
        host: str,
        port: int,
    ) -> FakeServer:
        calls.update(
            service=service,
            widget_dir=directory,
            host=host,
            port=port,
        )
        return FakeServer()

    monkeypatch.setattr(main_module, "StockfishEngine", fake_engine)
    monkeypatch.setattr(main_module, "create_server", fake_create_server)

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

    service = calls.pop("service")

    assert isinstance(service, main_module.ChessService)
    assert calls == {
        "stockfish": executable.resolve(),
        "widget_dir": widget_dir,
        "host": "0.0.0.0",
        "port": 9000,
        "runs": 1,
    }
    assert engine.starts == 1
    assert engine.stops == 1
    assert engine.running is False


def test_main_closes_service_when_server_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = RecordingEngine()

    class FailingServer:
        async def run_streamable_http_async(self) -> None:
            assert engine.running is True
            raise RuntimeError("server failed")

    monkeypatch.setattr(main_module, "StockfishEngine", lambda _: engine)
    monkeypatch.setattr(
        main_module,
        "create_server",
        lambda *_args, **_kwargs: FailingServer(),
    )

    with pytest.raises(RuntimeError, match="server failed"):
        main(["--widget_dir", str(make_widget_dir(tmp_path))])

    assert engine.starts == 1
    assert engine.stops == 1
    assert engine.running is False
