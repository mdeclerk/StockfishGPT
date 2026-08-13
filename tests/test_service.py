import asyncio

import chess
import chess.engine
import pytest
from fakes import FirstMoveEngine, RecordingEngine, candidate_info

from mcp_app.engine import Engine
from mcp_app.service.errors import (
    GameBusyError,
    GameNotFoundError,
    GameVersionError,
    InvalidMoveError,
    NothingToUndoError,
)
from mcp_app.service.models import Difficulty, GameStatus, ServiceStatus
from mcp_app.service.service import ChessService
from mcp_app.store import LocalGameStore
from mcp_app.store.errors import GameLeaseLostError, StoreDataError
from mcp_app.store.local import DEFAULT_GAME_TTL_SECONDS
from mcp_app.store.models import StoredGameState


def make_service(engine: Engine) -> ChessService:
    return ChessService(engine, LocalGameStore())


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RankedEngine(FirstMoveEngine):
    async def analyze(
        self,
        board: chess.Board,
        *,
        multipv: int,
        nodes: int,
    ) -> list[chess.engine.InfoDict]:
        self.calls.append((board.fen(en_passant="fen"), multipv, nodes))
        ranked = [
            candidate_info(chess.Move.from_uci("e7e5"), white_wdl=(250, 400, 350)),
            candidate_info(chess.Move.from_uci("c7c5"), white_wdl=(260, 400, 340)),
            candidate_info(chess.Move.from_uci("g8f6"), white_wdl=(300, 400, 300)),
            candidate_info(chess.Move.from_uci("d7d5"), white_wdl=(400, 400, 200)),
            candidate_info(chess.Move.from_uci("e7e6"), white_wdl=(500, 400, 100)),
        ]
        return ranked[:multipv]


class ScholarMateEngine(FirstMoveEngine):
    async def analyze(
        self,
        board: chess.Board,
        *,
        multipv: int,
        nodes: int,
    ) -> list[chess.engine.InfoDict]:
        self.calls.append((board.fen(en_passant="fen"), multipv, nodes))
        move = {1: "e7e5", 3: "b8c6", 5: "g8f6"}[len(board.move_stack)]
        return [candidate_info(chess.Move.from_uci(move))]


class RepetitionEngine(FirstMoveEngine):
    async def analyze(
        self,
        board: chess.Board,
        *,
        multipv: int,
        nodes: int,
    ) -> list[chess.engine.InfoDict]:
        self.calls.append((board.fen(en_passant="fen"), multipv, nodes))
        move = "g8f6" if board.piece_at(chess.G8) else "f6g8"
        return [candidate_info(chess.Move.from_uci(move))]


class FailingEngine(FirstMoveEngine):
    async def analyze(
        self,
        board: chess.Board,
        *,
        multipv: int,
        nodes: int,
    ) -> list[chess.engine.InfoDict]:
        self.calls.append((board.fen(en_passant="fen"), multipv, nodes))
        raise RuntimeError("engine failed")


class LineEngine(FirstMoveEngine):
    async def analyze(
        self,
        board: chess.Board,
        *,
        multipv: int,
        nodes: int,
    ) -> list[chess.engine.InfoDict]:
        self.calls.append((board.fen(en_passant="fen"), multipv, nodes))
        infos: list[chess.engine.InfoDict] = []
        for move in list(board.legal_moves)[:multipv]:
            replay = board.copy(stack=True)
            replay.push(move)
            reply = next(iter(replay.legal_moves), None)
            pv = [move, *([reply] if reply else [])]
            info = candidate_info(move)
            info["pv"] = pv
            infos.append(info)
        return infos


def test_analysis_engine_satisfies_the_service_protocol() -> None:
    assert isinstance(FirstMoveEngine(), Engine)


@pytest.mark.asyncio
async def test_service_reports_engine_liveness() -> None:
    engine = RecordingEngine()
    service = make_service(engine)

    assert await service.health_status() is ServiceStatus.ENGINE_UNAVAILABLE
    async with engine:
        assert await service.health_status() is ServiceStatus.OK
    assert await service.health_status() is ServiceStatus.ENGINE_UNAVAILABLE


@pytest.mark.asyncio
async def test_start_and_reset_return_complete_authoritative_state() -> None:
    service = make_service(FirstMoveEngine())

    started = await service.start_game(Difficulty.BEGINNER)
    reset = await service.reset_game(
        started.game_id,
        started.version,
        Difficulty.STRONG,
    )

    assert started.game_id
    assert started.version == 0
    assert started.ply_count == 0
    assert started.side_to_move.value == "white"
    assert "e2e4" in started.legal_moves
    assert reset.game_id == started.game_id
    assert reset.version == 1
    assert reset.difficulty is Difficulty.STRONG


@pytest.mark.asyncio
async def test_invalid_persisted_domain_values_are_typed_store_errors() -> None:
    store = LocalGameStore()
    service = ChessService(FirstMoveEngine(), store)
    record = StoredGameState("broken", 0, "impossible", (), (None,))
    async with store.try_lock(record.game_id):
        await store.set(record)

    with pytest.raises(StoreDataError, match="difficulty"):
        await service.get_game_state(record.game_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("difficulty", "candidate_count"),
    [
        (Difficulty.BEGINNER, 5),
        (Difficulty.CLUB, 3),
        (Difficulty.STRONG, 1),
    ],
)
async def test_play_white_move_applies_white_and_one_engine_reply(
    difficulty: Difficulty,
    candidate_count: int,
) -> None:
    engine = RankedEngine()
    service = make_service(engine)
    started = await service.start_game(difficulty)

    played = await service.play_white_move(started.game_id, 0, "e2e4")

    assert played.version == 1
    assert played.uci_history[0] == "e2e4"
    assert played.uci_history[1] in {"e7e5", "c7c5", "g8f6", "d7d5", "e7e6"}
    assert played.san_history[0] == "e4"
    assert played.side_to_move.value == "white"
    assert played.outlook is not None
    assert engine.calls[0][1:] == (candidate_count, 80_000)
    assert len(engine.calls) == 1


@pytest.mark.asyncio
async def test_white_terminal_move_skips_an_extra_analysis() -> None:
    engine = ScholarMateEngine()
    service = make_service(engine)
    game = await service.start_game(Difficulty.STRONG)
    for move in ("e2e4", "d1h5", "f1c4", "h5f7"):
        game = await service.play_white_move(game.game_id, game.version, move)

    assert game.status is GameStatus.WHITE_WINS_CHECKMATE
    assert game.uci_history[-1] == "h5f7"
    assert len(engine.calls) == 3


@pytest.mark.asyncio
async def test_engine_failure_leaves_white_move_uncommitted() -> None:
    service = make_service(FailingEngine())
    started = await service.start_game()

    with pytest.raises(RuntimeError, match="engine failed"):
        await service.play_white_move(started.game_id, 0, "e2e4")

    current = await service.get_game_state(started.game_id)
    assert current.version == 0
    assert current.uci_history == ()


@pytest.mark.asyncio
async def test_undo_white_move_removes_a_full_turn_and_restores_outlook() -> None:
    service = make_service(FirstMoveEngine())
    game = await service.start_game()
    first = await service.play_white_move(game.game_id, 0, "e2e4")
    first_outlook = first.outlook
    second_move = first.legal_moves[0]
    second = await service.play_white_move(game.game_id, first.version, second_move)

    undone = await service.undo_white_move(game.game_id, second.version)

    assert undone.uci_history == first.uci_history
    assert undone.outlook == first_outlook
    assert undone.version == 3


@pytest.mark.asyncio
async def test_analysis_returns_current_state_and_black_reply_without_mutating(
) -> None:
    engine = LineEngine()
    service = make_service(engine)
    game = await service.start_game()

    analysis = await service.analyze_position(game.game_id)
    current = await service.get_game_state(game.game_id)

    assert analysis.game == current
    assert current.version == 0
    assert len(analysis.candidates) == 3
    line = analysis.candidates[0].principal_variation
    assert len(line) == 2
    assert line[0].move_uci == analysis.candidates[0].move_uci
    assert line[1].move_uci
    assert engine.calls == [(game.fen, 3, 60_000)]


@pytest.mark.asyncio
async def test_full_history_detects_threefold_repetition() -> None:
    service = make_service(RepetitionEngine())
    game = await service.start_game(Difficulty.STRONG)
    for move in ("g1f3", "f3g1", "g1f3", "f3g1"):
        game = await service.play_white_move(game.game_id, game.version, move)

    assert game.status is GameStatus.DRAW_THREEFOLD_REPETITION
    assert game.ply_count == 8


def test_status_detects_fifty_move_rule() -> None:
    board = chess.Board("8/8/8/8/8/5k2/8/R3K3 w Q - 100 51")

    assert ChessService._status(board) is GameStatus.DRAW_FIFTY_MOVE_RULE


@pytest.mark.asyncio
async def test_concurrent_mutations_on_one_game_are_rejected_as_busy() -> None:
    class YieldingEngine(FirstMoveEngine):
        async def analyze(
            self,
            board: chess.Board,
            *,
            multipv: int,
            nodes: int,
        ) -> list[chess.engine.InfoDict]:
            await asyncio.sleep(0)
            return await super().analyze(board, multipv=multipv, nodes=nodes)

    engine = YieldingEngine()
    service = make_service(engine)
    game = await service.start_game()

    results = await asyncio.gather(
        service.play_white_move(game.game_id, 0, "e2e4"),
        service.play_white_move(game.game_id, 0, "d2d4"),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, GameBusyError) for result in results) == 1
    assert (await service.get_game_state(game.game_id)).version == 1


@pytest.mark.asyncio
async def test_busy_game_rejects_requests_before_any_engine_work() -> None:
    engine = FirstMoveEngine()
    store = LocalGameStore()
    service = ChessService(engine, store)
    game = await service.start_game()

    async with store.try_lock(game.game_id):
        with pytest.raises(GameBusyError, match="busy"):
            await service.play_white_move(game.game_id, 0, "e2e4")
        with pytest.raises(GameBusyError, match="busy"):
            await service.analyze_position(game.game_id)
        with pytest.raises(GameBusyError, match="busy"):
            await service.undo_white_move(game.game_id, 0)
        with pytest.raises(GameBusyError, match="busy"):
            await service.reset_game(game.game_id, 0)

    assert engine.calls == []


@pytest.mark.asyncio
async def test_lost_lock_lease_on_commit_is_a_version_conflict() -> None:
    class LeaseLosingStore(LocalGameStore):
        def __init__(self) -> None:
            super().__init__()
            self.sets = 0

        async def set(self, record: StoredGameState) -> None:
            self.sets += 1
            if self.sets > 1:
                raise GameLeaseLostError("lock lease expired")
            await super().set(record)

    store = LeaseLosingStore()
    service = ChessService(FirstMoveEngine(), store)
    game = await service.start_game()

    with pytest.raises(GameVersionError, match="current version is 0"):
        await service.play_white_move(game.game_id, 0, "e2e4")


@pytest.mark.asyncio
async def test_invalid_identity_move_version_and_empty_undo_are_rejected() -> None:
    service = make_service(FirstMoveEngine())
    game = await service.start_game()

    with pytest.raises(GameNotFoundError, match="not found"):
        await service.get_game_state("missing")
    with pytest.raises(GameVersionError, match="current version is 0"):
        await service.reset_game(game.game_id, 1)
    with pytest.raises(InvalidMoveError, match="not legal"):
        await service.play_white_move(game.game_id, 0, "e2e5")
    with pytest.raises(NothingToUndoError, match="no moves"):
        await service.undo_white_move(game.game_id, 0)


@pytest.mark.asyncio
async def test_expired_game_is_evicted_on_access() -> None:
    clock = ManualClock()
    store = LocalGameStore(clock=clock)
    service = ChessService(FirstMoveEngine(), store)
    game = await service.start_game()

    clock.advance(DEFAULT_GAME_TTL_SECONDS + 1)

    with pytest.raises(GameNotFoundError, match="not found"):
        await service.get_game_state(game.game_id)


@pytest.mark.asyncio
async def test_start_game_sweeps_expired_games() -> None:
    clock = ManualClock()
    store = LocalGameStore(clock=clock)
    service = ChessService(FirstMoveEngine(), store)
    stale = await service.start_game()
    clock.advance(1)
    fresh = await service.start_game()

    clock.advance(DEFAULT_GAME_TTL_SECONDS)
    replacement = await service.start_game()

    with pytest.raises(GameNotFoundError):
        await service.get_game_state(stale.game_id)
    assert await service.get_game_state(fresh.game_id) == fresh
    assert await service.get_game_state(replacement.game_id) == replacement


@pytest.mark.asyncio
async def test_start_game_evicts_least_recently_used_game_at_cap(
) -> None:
    store = LocalGameStore(max_games=2)
    service = ChessService(FirstMoveEngine(), store)
    oldest = await service.start_game()
    newest = await service.start_game()

    await service.get_game_state(newest.game_id)
    replacement = await service.start_game()

    with pytest.raises(GameNotFoundError):
        await service.get_game_state(oldest.game_id)
    assert await service.get_game_state(newest.game_id) == newest
    assert await service.get_game_state(replacement.game_id) == replacement
