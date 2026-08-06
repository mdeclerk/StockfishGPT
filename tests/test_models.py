from dataclasses import FrozenInstanceError

import chess
import chess.engine
import pytest
from pydantic import ValidationError

from mcp_app.chess_service.errors import InvalidAnalysisError
from mcp_app.chess_service.models import (
    Evaluation,
    Move,
    Side,
    WinDrawLoss,
)
from mcp_app.mcp.schemas import MoveSchema, WinDrawLossSchema

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_E4_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"


def info(
    *moves_uci: str,
    centipawns: int = 25,
    white_wdl: tuple[int, int, int] | None = None,
) -> chess.engine.InfoDict:
    built: chess.engine.InfoDict = {
        "score": chess.engine.PovScore(chess.engine.Cp(centipawns), chess.WHITE),
        "pv": [chess.Move.from_uci(uci) for uci in moves_uci],
    }
    if white_wdl is not None:
        built["wdl"] = chess.engine.PovWdl(
            chess.engine.Wdl(*white_wdl), chess.WHITE
        )
    return built


def test_domain_models_are_immutable() -> None:
    wdl = WinDrawLoss(white_wins=300, draws=400, black_wins=300)

    with pytest.raises(FrozenInstanceError):
        wdl.draws = 0


@pytest.mark.parametrize(
    "values",
    [(0, 0, 0), (-1, 1, 1)],
)
def test_wdl_rejects_invalid_values(values: tuple[int, int, int]) -> None:
    with pytest.raises(ValueError):
        WinDrawLoss(*values)


def test_every_python_chess_conversion_uses_from_stockfish() -> None:
    board = chess.Board(AFTER_E4_FEN)
    evaluation = Evaluation.from_stockfish(
        board,
        [info("e7e5", white_wdl=(250, 400, 350))],
    )

    assert Side.from_stockfish(chess.BLACK) is Side.BLACK
    assert evaluation.best.move_uci == "e7e5"
    assert evaluation.best.wdl.black_wins == 350


def test_move_carries_both_notations_and_white_perspective() -> None:
    board = chess.Board(AFTER_E4_FEN)

    move = Move.from_stockfish(
        board,
        info("e7e5", centipawns=10, white_wdl=(250, 400, 350)),
    )

    assert move.move_uci == "e7e5"
    assert move.move_san == "e5"
    assert move.centipawns == 10
    assert move.mate_in is None
    assert move.wdl.white_expectation == pytest.approx(0.45)
    assert move.wdl.expectation_for(Side.BLACK) == pytest.approx(0.55)


def test_missing_wdl_falls_back_to_stockfish_score_model() -> None:
    move = Move.from_stockfish(chess.Board(START_FEN), info("e2e4"))

    assert move.wdl.total == 1000


def test_principal_variation_is_capped_at_six_plies() -> None:
    move = Move.from_stockfish(
        chess.Board(START_FEN),
        info("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "e1g1"),
    )

    assert [item.move_uci for item in move.principal_variation] == [
        "e2e4",
        "e7e5",
        "g1f3",
        "b8c6",
        "f1c4",
        "g8f6",
    ]
    assert [item.move_san for item in move.principal_variation] == [
        "e4",
        "e5",
        "Nf3",
        "Nc6",
        "Bc4",
        "Nf6",
    ]


@pytest.mark.parametrize(
    "broken",
    [
        {"score": chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE)},
        {"pv": [chess.Move.from_uci("e2e4")]},
    ],
)
def test_incomplete_stockfish_candidate_is_rejected(
    broken: chess.engine.InfoDict,
) -> None:
    with pytest.raises(InvalidAnalysisError, match="missing a score or principal"):
        Move.from_stockfish(chess.Board(START_FEN), broken)


def test_illegal_principal_variation_is_rejected() -> None:
    with pytest.raises(InvalidAnalysisError, match="illegal move"):
        Move.from_stockfish(
            chess.Board(START_FEN), info("e2e4", "e2e4")
        )


def test_evaluation_normalizes_fen_and_orders_candidates() -> None:
    evaluation = Evaluation.from_stockfish(
        chess.Board(AFTER_E4_FEN),
        [
            info("e7e5", white_wdl=(250, 400, 350)),
            info("c7c5", white_wdl=(260, 400, 340)),
        ],
    )

    assert evaluation.fen == AFTER_E4_FEN
    assert evaluation.side_to_move is Side.BLACK
    assert [move.move_uci for move in evaluation.candidates] == ["e7e5", "c7c5"]


def test_empty_stockfish_analysis_is_rejected() -> None:
    with pytest.raises(InvalidAnalysisError, match="no candidate moves"):
        Evaluation.from_stockfish(chess.Board(START_FEN), [])


def test_mcp_schemas_convert_domain_models_and_remain_frozen() -> None:
    evaluation = Evaluation.from_stockfish(
        chess.Board(AFTER_E4_FEN),
        [info("e7e5", white_wdl=(250, 400, 350))],
    )

    response = MoveSchema.from_domain(evaluation.best)

    assert response.wdl.white_expectation == pytest.approx(0.45)
    assert response.principal_variation[0].model_dump() == {
        "move_uci": "e7e5",
        "move_san": "e5",
    }
    with pytest.raises(ValidationError):
        response.move_uci = "c7c5"


def test_wire_wdl_still_validates_independent_input() -> None:
    with pytest.raises(ValidationError, match="total must be positive"):
        WinDrawLossSchema(white_wins=0, draws=0, black_wins=0)
