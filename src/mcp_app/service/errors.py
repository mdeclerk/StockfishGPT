"""Expected chess-service failures."""


class ChessServiceError(RuntimeError):
    """A service failure whose message is safe to show the user."""


class PositionError(ChessServiceError):
    """The requested position cannot be worked with."""


class TerminalPositionError(PositionError):
    def __init__(self, fen: str) -> None:
        self.fen = fen
        super().__init__(f"The game is already over in this position: {fen}")


class InvalidAnalysisError(ChessServiceError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Stockfish analysis failed: {detail}")


class GameNotFoundError(ChessServiceError):
    def __init__(self, game_id: str) -> None:
        self.game_id = game_id
        super().__init__(f"Chess game {game_id!r} was not found.")


class GameBusyError(ChessServiceError):
    def __init__(self, game_id: str) -> None:
        self.game_id = game_id
        super().__init__(
            f"Chess game {game_id!r} is busy with another request; retry in a moment."
        )


class GameVersionError(ChessServiceError):
    def __init__(self, game_id: str, version: int, current: int) -> None:
        self.game_id = game_id
        self.version = version
        self.current = current
        super().__init__(
            f"Chess game {game_id!r} changed: version {version} was supplied "
            f"but the current version is {current}."
        )


class InvalidMoveError(ChessServiceError):
    def __init__(self, move_uci: str) -> None:
        self.move_uci = move_uci
        super().__init__(f"The move {move_uci!r} is not legal in this game.")


class NotPlayersTurnError(ChessServiceError):
    def __init__(self) -> None:
        super().__init__("It is not White's turn in this game.")


class NothingToUndoError(ChessServiceError):
    def __init__(self) -> None:
        super().__init__("There are no moves to undo in this game.")
