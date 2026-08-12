import asyncio
import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

import chess
import chess.engine
import pytest

import mcp_app.stockfish.engine as engine_module
from mcp_app.service.models import Difficulty
from mcp_app.service.service import ChessService
from mcp_app.stockfish.engine import StockfishEngine
from mcp_app.stockfish.errors import (
    EngineNotFoundError,
    EngineNotStartedError,
    EngineProcessError,
    EngineRestartingError,
    EngineTimeoutError,
)

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_E4_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"


def candidate_info(move_uci: str) -> chess.engine.InfoDict:
    return {
        "score": chess.engine.PovScore(chess.engine.Cp(15), chess.WHITE),
        "wdl": chess.engine.PovWdl(
            chess.engine.Wdl(300, 400, 300), chess.WHITE
        ),
        "pv": [chess.Move.from_uci(move_uci)],
    }


def black_candidate_infos() -> list[chess.engine.InfoDict]:
    return [
        candidate_info("e7e5"),
        candidate_info("c7c5"),
        candidate_info("g8f6"),
        candidate_info("d7d5"),
        candidate_info("e7e6"),
    ]


class FakeTransport:
    def __init__(self) -> None:
        self.closed = False
        self.returncode: int | None = None

    def close(self) -> None:
        self.closed = True

    def get_returncode(self) -> int | None:
        return self.returncode

    def crash(self) -> None:
        self.returncode = -9


class FakeProtocol:
    def __init__(self) -> None:
        self.infos = black_candidate_infos()
        self.configure_calls: list[Mapping[str, object]] = []
        self.analyse_calls: list[tuple[int | None, int | None]] = []
        self.games: list[object | None] = []
        self.quit_calls = 0
        self.analysis_delay = 0.0
        self.analysis_error: Exception | None = None
        self.active_analyses = 0
        self.maximum_active_analyses = 0
        self.return_single = False

    async def configure(self, options: Mapping[str, object]) -> None:
        self.configure_calls.append(options)

    async def analyse(
        self,
        board: chess.Board,
        limit: chess.engine.Limit,
        *,
        multipv: int | None = None,
        info: chess.engine.Info = chess.engine.INFO_ALL,
        game: object | None = None,
    ) -> chess.engine.InfoDict | list[chess.engine.InfoDict]:
        del board, info
        self.games.append(game)
        self.analyse_calls.append((limit.nodes, multipv))
        self.active_analyses += 1
        self.maximum_active_analyses = max(
            self.maximum_active_analyses, self.active_analyses
        )
        try:
            if self.analysis_delay:
                await asyncio.sleep(self.analysis_delay)
            if self.analysis_error:
                raise self.analysis_error
            selected = self.infos[: multipv or 1]
            return selected[0] if self.return_single else selected
        finally:
            self.active_analyses -= 1

    async def quit(self) -> None:
        self.quit_calls += 1


def fake_engine(
    executable: Path | None = None,
) -> tuple[StockfishEngine, FakeProtocol, FakeTransport, list[str]]:
    protocol = FakeProtocol()
    transport = FakeTransport()
    opened_paths: list[str] = []

    async def factory(path: str) -> tuple[FakeTransport, FakeProtocol]:
        opened_paths.append(path)
        return transport, protocol

    return (
        StockfishEngine(executable, factory=factory),
        protocol,
        transport,
        opened_paths,
    )


def restartable_engine() -> tuple[
    StockfishEngine, list[FakeTransport], list[FakeProtocol]
]:
    transports: list[FakeTransport] = []
    protocols: list[FakeProtocol] = []

    async def factory(path: str) -> tuple[FakeTransport, FakeProtocol]:
        del path
        transports.append(FakeTransport())
        protocols.append(FakeProtocol())
        return transports[-1], protocols[-1]

    return StockfishEngine(factory=factory), transports, protocols


@pytest.fixture(autouse=True)
def stockfish_on_path(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if request.node.get_closest_marker("integration") is None:
        monkeypatch.setattr(shutil, "which", lambda executable: sys.executable)


@pytest.mark.asyncio
async def test_start_and_close_are_idempotent() -> None:
    engine, protocol, transport, opened_paths = fake_engine()

    await engine.start()
    await engine.start()

    assert opened_paths == [sys.executable]
    assert protocol.configure_calls == [
        {"Threads": 1, "Hash": 64, "UCI_ShowWDL": True}
    ]
    assert engine.is_alive is True

    await engine.close()
    await engine.close()

    assert protocol.quit_calls == 1
    assert transport.closed is True
    assert engine.is_alive is False


@pytest.mark.asyncio
async def test_discarded_engine_stays_owned_for_restart() -> None:
    engine, transports, _ = restartable_engine()
    await engine.start()
    transports[0].crash()
    board = chess.Board(AFTER_E4_FEN)

    await engine.analyze(board, multipv=1, nodes=100)
    transports[1].crash()

    assert engine.is_alive is False
    # Still owned: the next analysis restarts instead of raising
    # EngineNotStartedError.
    await engine.analyze(board, multipv=1, nodes=100)
    assert len(transports) == 3
    assert engine.is_alive is True
    await engine.close()


@pytest.mark.asyncio
async def test_close_ignores_engine_quit_failure() -> None:
    engine, protocol, transport, _ = fake_engine()

    async def failing_quit() -> None:
        raise chess.engine.EngineError("engine dead")

    protocol.quit = failing_quit
    await engine.start()

    await engine.close()

    assert transport.closed is True
    assert engine.is_alive is False


@pytest.mark.asyncio
async def test_explicit_executable_is_used_without_path_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        shutil,
        "which",
        lambda executable: pytest.fail("PATH must not be searched"),
    )
    executable = Path(sys.executable)
    engine, _, _, opened_paths = fake_engine(executable)

    await engine.start()

    assert opened_paths == [str(executable)]
    await engine.close()


@pytest.mark.asyncio
async def test_invalid_explicit_executable_never_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        shutil,
        "which",
        lambda executable: pytest.fail("PATH must not be searched"),
    )
    engine = StockfishEngine(tmp_path / "missing")

    with pytest.raises(EngineNotFoundError, match="not usable"):
        await engine.start()


@pytest.mark.asyncio
async def test_analyze_returns_raw_normalized_info_list() -> None:
    engine, protocol, _, _ = fake_engine()
    protocol.return_single = True
    await engine.start()

    infos = await engine.analyze(
        chess.Board(AFTER_E4_FEN), multipv=1, nodes=1234
    )

    assert isinstance(infos, list)
    assert infos[0]["pv"][0].uci() == "e7e5"
    assert protocol.analyse_calls == [(1234, 1)]
    await engine.close()


@pytest.mark.asyncio
async def test_concurrent_analysis_is_serialized() -> None:
    engine, protocol, _, _ = fake_engine()
    protocol.analysis_delay = 0.01
    await engine.start()
    board = chess.Board(AFTER_E4_FEN)

    await asyncio.gather(
        engine.analyze(board, multipv=1, nodes=100),
        engine.analyze(board, multipv=1, nodes=100),
    )

    assert protocol.maximum_active_analyses == 1
    await engine.close()


@pytest.mark.asyncio
async def test_dead_engine_restarts_once_for_concurrent_callers() -> None:
    engine, transports, _ = restartable_engine()
    await engine.start()
    transports[0].crash()
    board = chess.Board(AFTER_E4_FEN)

    await asyncio.gather(
        engine.analyze(board, multipv=1, nodes=100),
        engine.analyze(board, multipv=1, nodes=100),
        engine.analyze(board, multipv=1, nodes=100),
    )

    assert len(transports) == 2
    assert transports[0].closed is True
    assert engine.is_alive is True
    await engine.close()


@pytest.mark.asyncio
async def test_analysis_failure_discards_and_replaces_process() -> None:
    engine, transports, protocols = restartable_engine()
    await engine.start()
    protocols[0].analysis_error = chess.engine.EngineError("engine stopped")
    board = chess.Board(AFTER_E4_FEN)

    with pytest.raises(EngineProcessError):
        await engine.analyze(board, multipv=1, nodes=100)

    assert transports[0].closed is True
    assert engine.is_alive is False

    await engine.analyze(board, multipv=1, nodes=100)
    assert len(transports) == 2
    await engine.close()


@pytest.mark.asyncio
async def test_failed_restart_enters_cooldown() -> None:
    transports: list[FakeTransport] = []
    spawns = 0

    async def factory(path: str) -> tuple[FakeTransport, FakeProtocol]:
        del path
        nonlocal spawns
        spawns += 1
        if spawns > 1:
            raise OSError("stockfish is gone")
        transports.append(FakeTransport())
        return transports[-1], FakeProtocol()

    engine = StockfishEngine(factory=factory)
    await engine.start()
    transports[0].crash()
    board = chess.Board(AFTER_E4_FEN)

    with pytest.raises(EngineProcessError):
        await engine.analyze(board, multipv=1, nodes=100)
    with pytest.raises(EngineRestartingError) as raised:
        await engine.analyze(board, multipv=1, nodes=100)

    assert spawns == 2
    assert raised.value.retry_after_seconds > 0
    await engine.close()


@pytest.mark.asyncio
async def test_analysis_requires_started_engine() -> None:
    engine, _, _, _ = fake_engine()

    with pytest.raises(EngineNotStartedError):
        await engine.analyze(chess.Board(START_FEN), multipv=1, nodes=100)


@pytest.mark.asyncio
async def test_analysis_timeout_is_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_module, "_ENGINE_TIMEOUT_SECONDS", 0.001)
    engine, protocol, _, _ = fake_engine()
    protocol.analysis_delay = 0.05
    await engine.start()

    with pytest.raises(EngineTimeoutError) as raised:
        await engine.analyze(
            chess.Board(AFTER_E4_FEN), multipv=1, nodes=100
        )

    assert raised.value.operation == "analysis"
    await engine.close()


@pytest.mark.asyncio
async def test_each_analysis_uses_a_fresh_game_identity() -> None:
    engine, protocol, _, _ = fake_engine()
    await engine.start()
    board = chess.Board(AFTER_E4_FEN)

    for _ in range(3):
        await engine.analyze(board, multipv=1, nodes=100)

    assert all(game is not None for game in protocol.games)
    assert len(set(map(id, protocol.games))) == 3
    await engine.close()


@pytest.mark.asyncio
async def test_missing_stockfish_on_path_is_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda executable: None)

    with pytest.raises(EngineNotFoundError, match="not found on PATH"):
        await StockfishEngine().start()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_STOCKFISH_TESTS") != "1",
    reason="set RUN_STOCKFISH_TESTS=1 to exercise a local Stockfish binary",
)
async def test_real_stockfish_analyzes_initial_position() -> None:
    if shutil.which("stockfish") is None:
        pytest.fail("RUN_STOCKFISH_TESTS=1 but no Stockfish executable was found")

    async with StockfishEngine() as engine:
        infos = await engine.analyze(
            chess.Board(START_FEN), multipv=1, nodes=60_000
        )

    legal = {move.uci() for move in chess.Board(START_FEN).legal_moves}
    assert infos[0]["pv"][0].uci() in legal


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_STOCKFISH_TESTS") != "1",
    reason="set RUN_STOCKFISH_TESTS=1 to exercise a local Stockfish binary",
)
async def test_real_stockfish_service_repeats_itself() -> None:
    if shutil.which("stockfish") is None:
        pytest.fail("RUN_STOCKFISH_TESTS=1 but no Stockfish executable was found")

    async with StockfishEngine() as engine:
        service = ChessService(engine)
        choices: list[str] = []
        for _ in range(4):
            game = await service.start_game(Difficulty.CLUB)
            played = await service.play_white_move(game.game_id, 0, "e2e4")
            choices.append(played.uci_history[1])

    assert len(set(choices)) == 1
