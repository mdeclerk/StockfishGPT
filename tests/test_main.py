from pathlib import Path

import pytest
from fakes import RecordingEngine

import mcp_app.main as main_module
from mcp_app.main import _load_settings, main

CONFIG_ENVIRONMENT = ("HOST", "PORT", "WIDGET_DIR", "STOCKFISH_PATH")


@pytest.fixture(autouse=True)
def clear_config_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CONFIG_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


def make_widget_dir(tmp_path: Path, name: str = "dist") -> Path:
    directory = tmp_path / name
    directory.mkdir(parents=True)
    (directory / "index.html").write_text("<!doctype html><title>Built</title>")
    return directory


def make_executable(tmp_path: Path, name: str = "stockfish-test") -> Path:
    executable = tmp_path / name
    executable.write_text("test")
    executable.chmod(0o755)
    return executable


def test_settings_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    make_widget_dir(tmp_path, "widget/dist")
    monkeypatch.chdir(tmp_path)

    settings = _load_settings([])

    assert settings.widget_dir == Path("widget/dist")
    assert settings.stockfish_path is None
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000


def test_settings_load_plain_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = make_widget_dir(tmp_path)
    executable = make_executable(tmp_path)
    monkeypatch.setenv("WIDGET_DIR", str(directory))
    monkeypatch.setenv("STOCKFISH_PATH", str(executable))
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "9000")

    settings = _load_settings([])

    assert settings.widget_dir == directory
    assert settings.stockfish_path == executable.resolve()
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000


def test_cli_overrides_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment_directory = make_widget_dir(tmp_path, "environment-widget")
    environment_executable = make_executable(tmp_path, "environment-stockfish")
    cli_directory = make_widget_dir(tmp_path, "cli-widget")
    cli_executable = make_executable(tmp_path, "cli-stockfish")
    monkeypatch.setenv("WIDGET_DIR", str(environment_directory))
    monkeypatch.setenv("STOCKFISH_PATH", str(environment_executable))
    monkeypatch.setenv("HOST", "127.0.0.2")
    monkeypatch.setenv("PORT", "7000")

    settings = _load_settings(
        [
            "--widget-dir",
            str(cli_directory),
            "--stockfish-path",
            str(cli_executable),
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
        ]
    )

    assert settings.widget_dir == cli_directory
    assert settings.stockfish_path == cli_executable.resolve()
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000


def test_dotenv_file_is_not_loaded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    make_widget_dir(tmp_path, "widget/dist")
    (tmp_path / ".env").write_text("PORT=9000\n")
    monkeypatch.chdir(tmp_path)

    settings = _load_settings([])

    assert settings.port == 8000


def test_cli_accepts_widget_directory_shortcut(tmp_path: Path) -> None:
    directory = make_widget_dir(tmp_path)

    settings = _load_settings(["--wdir", str(directory)])

    assert settings.widget_dir == directory


@pytest.mark.parametrize("option", ["--widget_dir", "--stockfish_path"])
def test_cli_rejects_removed_underscore_options(
    option: str,
    tmp_path: Path,
) -> None:
    directory = make_widget_dir(tmp_path)

    with pytest.raises(SystemExit) as raised:
        _load_settings(["--widget-dir", str(directory), option, "value"])

    assert raised.value.code == 2


def test_cli_help_uses_existing_descriptions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        _load_settings(["--help"])

    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "Run the StockfishGPT MCP app." in output
    assert "--widget-dir, --wdir" in output
    assert "Vite build output directory containing index.html" in output
    assert "--stockfish-path" in output
    assert "Path to the Stockfish executable" in output
    assert "Host interface to bind" in output
    assert "Port to bind" in output
    normalized_output = " ".join(output.split())
    assert "[env: WIDGET_DIR]" in normalized_output
    assert "[env: STOCKFISH_PATH]" in normalized_output
    assert "[env: HOST]" in normalized_output
    assert "[env: PORT]" in normalized_output


@pytest.mark.parametrize("kind", ["missing", "missing_bundle"])
def test_widget_setting_rejects_unusable_directory(
    kind: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "missing"
    if kind == "missing_bundle":
        target.mkdir()

    with pytest.raises(SystemExit) as raised:
        _load_settings(["--widget-dir", str(target)])

    assert raised.value.code == 2
    assert "mcp-app: configuration error" in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["missing", "directory", "non_executable"])
def test_stockfish_setting_rejects_unusable_target(
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

    with pytest.raises(SystemExit) as raised:
        _load_settings(
            [
                "--widget-dir",
                str(directory),
                "--stockfish-path",
                str(target),
            ]
        )

    assert raised.value.code == 2


@pytest.mark.parametrize("port", ["0", "65536", "not-a-number"])
def test_port_setting_rejects_invalid_values(
    port: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    directory = make_widget_dir(tmp_path)

    with pytest.raises(SystemExit) as raised:
        _load_settings(["--widget-dir", str(directory), "--port", port])

    assert raised.value.code == 2
    assert "Traceback" not in capsys.readouterr().err


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
            "--widget-dir",
            str(widget_dir),
            "--stockfish-path",
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
        main(["--widget-dir", str(make_widget_dir(tmp_path))])

    assert engine.starts == 1
    assert engine.stops == 1
    assert engine.running is False
