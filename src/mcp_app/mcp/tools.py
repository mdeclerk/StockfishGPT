"""MCP chess tools and their registration."""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from mcp_app.mcp.resources import WIDGET_URI
from mcp_app.mcp.schemas import GameStateSchema, PositionAnalysisSchema
from mcp_app.service import ChessService, Difficulty

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
CREATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
MUTATING = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)

START_GAME_DESCRIPTION = (
    "Immediately call for every play/start request when no game_id exists; do "
    "not reply first or claim success unless this tool returns successfully. "
    "Opens the board with the user as White and needs no board, FEN, or game_id. "
    "Use once per chat; the board handles reset, moves, and undo."
)
RESET_GAME_DESCRIPTION = (
    "Restart the active game at the chosen difficulty from the board."
)
PLAY_WHITE_MOVE_DESCRIPTION = (
    "Play one legal White move, then play Stockfish's reply unless White's move "
    "ends the game."
)
UNDO_WHITE_MOVE_DESCRIPTION = (
    "Undo the last White move and Stockfish reply, or only White's move if it "
    "ended the game."
)
GET_GAME_STATE_DESCRIPTION = (
    "Fetch the fresh authoritative state for facts, status, history, or rules."
)
ANALYZE_POSITION_DESCRIPTION = (
    "Analyze the fresh position for advice, plans, tactics, evaluation, or "
    "variations. `game` is the authoritative snapshot; candidates are best-first."
)


def register_tools(mcp: FastMCP, service: ChessService) -> None:
    """Register the public tool contract."""

    @mcp.tool(
        name="start_game",
        title="Start Chess Game",
        description=START_GAME_DESCRIPTION,
        annotations=CREATE,
        meta={
            "ui": {
                "resourceUri": WIDGET_URI,
                "visibility": ["model"],
            },
            "openai/outputTemplate": WIDGET_URI,
        },
        structured_output=True,
    )
    async def start_game(
        difficulty: Difficulty = Difficulty.CLUB,
    ) -> GameStateSchema:
        return GameStateSchema.from_domain(await service.start_game(difficulty))

    @mcp.tool(
        name="reset_game",
        title="Reset Chess Game",
        description=RESET_GAME_DESCRIPTION,
        annotations=DESTRUCTIVE,
        meta={"ui": {"visibility": ["app"]}},
        structured_output=True,
    )
    async def reset_game(
        game_id: str,
        version: int,
        difficulty: Difficulty = Difficulty.CLUB,
    ) -> GameStateSchema:
        return GameStateSchema.from_domain(
            await service.reset_game(game_id, version, difficulty)
        )

    @mcp.tool(
        name="play_white_move",
        title="Play White Move",
        description=PLAY_WHITE_MOVE_DESCRIPTION,
        annotations=MUTATING,
        meta={"ui": {"visibility": ["app"]}},
        structured_output=True,
    )
    async def play_white_move(
        game_id: str,
        version: int,
        move_uci: str,
    ) -> GameStateSchema:
        return GameStateSchema.from_domain(
            await service.play_white_move(game_id, version, move_uci)
        )

    @mcp.tool(
        name="undo_white_move",
        title="Undo White Move",
        description=UNDO_WHITE_MOVE_DESCRIPTION,
        annotations=DESTRUCTIVE,
        meta={"ui": {"visibility": ["app"]}},
        structured_output=True,
    )
    async def undo_white_move(game_id: str, version: int) -> GameStateSchema:
        return GameStateSchema.from_domain(
            await service.undo_white_move(game_id, version)
        )

    @mcp.tool(
        name="get_game_state",
        title="Get Chess Game State",
        description=GET_GAME_STATE_DESCRIPTION,
        annotations=READ_ONLY,
        meta={"ui": {"visibility": ["model", "app"]}},
        structured_output=True,
    )
    async def get_game_state(game_id: str) -> GameStateSchema:
        return GameStateSchema.from_domain(
            await service.get_game_state(game_id)
        )

    @mcp.tool(
        name="analyze_position",
        title="Analyze Chess Position",
        description=ANALYZE_POSITION_DESCRIPTION,
        annotations=READ_ONLY,
        meta={"ui": {"visibility": ["model", "app"]}},
        structured_output=True,
    )
    async def analyze_position(game_id: str) -> PositionAnalysisSchema:
        return PositionAnalysisSchema.from_domain(
            await service.analyze_position(game_id)
        )
