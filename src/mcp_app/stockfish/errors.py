"""Expected Stockfish process failures."""

from pathlib import Path


class EngineError(RuntimeError):
    """An expected Stockfish failure."""


class EngineBrokenError(EngineError):
    """The current process can no longer be trusted."""


class EngineTimeoutError(EngineBrokenError):
    def __init__(self, operation: str, timeout_seconds: float) -> None:
        self.operation = operation
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Stockfish {operation} timed out after {timeout_seconds:g} seconds"
        )


class EngineProcessError(EngineBrokenError):
    def __init__(self, operation: str, detail: str) -> None:
        self.operation = operation
        self.detail = detail
        super().__init__(f"Stockfish {operation} failed: {detail}")


class EngineUnavailableError(EngineError):
    """No usable Stockfish process is available."""


class EngineNotFoundError(EngineUnavailableError):
    def __init__(self, executable: Path | None = None) -> None:
        self.executable = executable
        if executable is None:
            message = "Stockfish executable was not found on PATH"
        else:
            message = f"Stockfish executable is not usable: {executable}"
        super().__init__(message)


class EngineNotStartedError(EngineUnavailableError):
    def __init__(self) -> None:
        super().__init__("Stockfish has not been started")


class EngineRestartingError(EngineUnavailableError):
    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            "Stockfish could not be restarted; retry in "
            f"{retry_after_seconds:.1f} seconds"
        )
