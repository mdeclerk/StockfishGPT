import type {
  Difficulty,
  GameState,
  PositionAnalysis,
} from "./types";

export type HostDisplayMode = "inline" | "pip" | "fullscreen";

/** Authoritative game access. */
export interface ChessBridge {
  open(): Promise<GameState>;
  getGameState(gameId: string): Promise<GameState>;
  resetGame(
    gameId: string,
    version: number,
    difficulty: Difficulty,
  ): Promise<GameState>;
  playMove(
    gameId: string,
    version: number,
    moveUci: string,
  ): Promise<GameState>;
  analyzePosition(gameId: string): Promise<PositionAnalysis>;
  undo(gameId: string, version: number): Promise<GameState>;
}

/** Optional ChatGPT integration. */
export interface HostBridge {
  anchorBoard(): Promise<HostDisplayMode>;
  getDisplayMode(): HostDisplayMode;
  onDisplayModeChange(listener: (mode: HostDisplayMode) => void): () => void;
  requestAdvice(): Promise<void>;
}
