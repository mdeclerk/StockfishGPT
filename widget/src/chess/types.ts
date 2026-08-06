export const DIFFICULTIES = ["beginner", "club", "strong"] as const;
export const SIDES = ["white", "black"] as const;
export const GAME_STATUSES = [
  "in_progress",
  "white_wins_checkmate",
  "black_wins_checkmate",
  "draw_stalemate",
  "draw_insufficient_material",
  "draw_fifty_move_rule",
  "draw_threefold_repetition",
] as const;

export type Difficulty = (typeof DIFFICULTIES)[number];
export type Side = (typeof SIDES)[number];
export type GameStatus = (typeof GAME_STATUSES)[number];

export const STARTING_FEN =
  "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

/** A White-perspective forecast. */
export interface WinDrawLoss {
  white_wins: number;
  draws: number;
  black_wins: number;
  white_expectation: number;
}

/** One move in an engine principal variation. */
export interface VariationMove {
  move_uci: string;
  move_san: string;
}

/** An engine candidate and its resulting evaluation. */
export interface Move {
  move_uci: string;
  move_san: string;
  centipawns: number | null;
  mate_in: number | null;
  wdl: WinDrawLoss;
  principal_variation: VariationMove[];
}

/** The complete authoritative state rendered by the widget. */
export interface GameState {
  game_id: string;
  version: number;
  difficulty: Difficulty;
  fen: string;
  side_to_move: Side;
  status: GameStatus;
  is_in_check: boolean;
  legal_moves: string[];
  uci_history: string[];
  san_history: string[];
  ply_count: number;
  outlook: WinDrawLoss | null;
}

export interface PositionAnalysis {
  game: GameState;
  candidates: Move[];
}
