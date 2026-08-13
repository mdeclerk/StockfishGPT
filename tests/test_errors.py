import pytest

from mcp_app.engine.errors import (
    EngineBrokenError,
    EngineError,
    EngineNotFoundError,
    EngineNotStartedError,
    EngineProcessError,
    EngineRestartingError,
    EngineTimeoutError,
    EngineUnavailableError,
)
from mcp_app.service.errors import (
    ChessServiceError,
    GameBusyError,
    GameNotFoundError,
    GameVersionError,
    InvalidAnalysisError,
    InvalidMoveError,
    PositionError,
    TerminalPositionError,
)
from mcp_app.store.errors import (
    GameLeaseLostError,
    GameLockedError,
    StoreDataError,
    StoreError,
    StoreUnavailableError,
)


@pytest.mark.parametrize(
    ("error", "branch"),
    [
        (TerminalPositionError, PositionError),
        (InvalidAnalysisError, ChessServiceError),
        (GameBusyError, ChessServiceError),
        (GameNotFoundError, ChessServiceError),
        (GameVersionError, ChessServiceError),
        (InvalidMoveError, ChessServiceError),
        (EngineTimeoutError, EngineBrokenError),
        (EngineProcessError, EngineBrokenError),
        (EngineNotFoundError, EngineUnavailableError),
        (EngineNotStartedError, EngineUnavailableError),
        (EngineRestartingError, EngineUnavailableError),
        (StoreDataError, StoreError),
        (StoreUnavailableError, StoreError),
        (GameLockedError, StoreError),
        (GameLeaseLostError, StoreError),
    ],
)
def test_errors_belong_to_their_layered_branches(
    error: type[Exception],
    branch: type[Exception],
) -> None:
    assert issubclass(error, branch)


def test_only_broken_engine_errors_condemn_the_process() -> None:
    assert issubclass(EngineBrokenError, EngineError)
    assert issubclass(EngineUnavailableError, EngineError)
    assert not issubclass(EngineUnavailableError, EngineBrokenError)


def test_restart_error_reports_retry_delay() -> None:
    error = EngineRestartingError(2.5)

    assert error.retry_after_seconds == 2.5
    assert "2.5 seconds" in str(error)
