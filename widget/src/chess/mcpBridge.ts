import { App } from "@modelcontextprotocol/ext-apps";

import type {
  ChessBridge,
  HostBridge,
  HostDisplayMode,
} from "./bridge";
import type {
  Difficulty,
  GameState,
  PositionAnalysis,
} from "./types";
import {
  isGameState,
  isPositionAnalysis,
  isRecord,
} from "./validation";

interface ToolResult {
  content?: { type: string; text?: string }[];
  structuredContent?: unknown;
  isError?: boolean;
}

export class McpToolError extends Error {
  readonly tool: string;

  constructor(tool: string, message: string) {
    super(message);
    this.name = "McpToolError";
    this.tool = tool;
  }
}

export interface AppsClient {
  connect(): Promise<void>;
  onToolResult(listener: (result: ToolResult) => void): void;
  onDisplayModeChange(listener: (mode: HostDisplayMode) => void): () => void;
  callTool(name: string, arguments_: Record<string, unknown>): Promise<ToolResult>;
  canSendMessage(): boolean;
  requestPictureInPicture(): Promise<HostDisplayMode>;
  sendMessage(prompt: string): Promise<void>;
}

/** MCP Apps adapter. */
export class McpChessBridge implements ChessBridge, HostBridge {
  readonly #client: AppsClient;
  readonly #connected: Promise<void>;
  readonly #startedGame: Promise<GameState>;
  #resolveStartedGame!: (game: GameState) => void;
  #rejectStartedGame!: (error: Error) => void;
  #startSettled = false;
  #displayMode: HostDisplayMode = "inline";
  readonly #displayModeListeners = new Set<(mode: HostDisplayMode) => void>();

  constructor(client: AppsClient = new StandardAppsClient()) {
    this.#client = client;
    this.#startedGame = new Promise((resolve, reject) => {
      this.#resolveStartedGame = resolve;
      this.#rejectStartedGame = reject;
    });
    client.onToolResult((result) => {
      if (this.#startSettled) return;
      this.#startSettled = true;
      if (result.isError) {
        this.#rejectStartedGame(toolError(result, "start_game"));
        return;
      }
      const game = gameStateFromStructuredContent(result.structuredContent);
      if (game) this.#resolveStartedGame(game);
      else this.#rejectStartedGame(invalidStructuredContent(result, "start_game"));
    });
    client.onDisplayModeChange((mode) => this.#setDisplayMode(mode));
    this.#connected = client.connect();
  }

  async open(): Promise<GameState> {
    await this.#connected;
    const started = await this.#startedGame;
    return this.getGameState(started.game_id);
  }

  async getGameState(gameId: string): Promise<GameState> {
    return this.#gameTool("get_game_state", { game_id: gameId });
  }

  async resetGame(
    gameId: string,
    version: number,
    difficulty: Difficulty,
  ): Promise<GameState> {
    return this.#gameTool("reset_game", {
      game_id: gameId,
      version,
      difficulty,
    });
  }

  async playMove(
    gameId: string,
    version: number,
    moveUci: string,
  ): Promise<GameState> {
    return this.#gameTool("play_white_move", {
      game_id: gameId,
      version,
      move_uci: moveUci,
    });
  }

  async analyzePosition(gameId: string): Promise<PositionAnalysis> {
    await this.#connected;
    const result = await this.#client.callTool("analyze_position", {
      game_id: gameId,
    });
    if (result.isError) throw toolError(result, "analyze_position");
    const normalized = normalizePositionAnalysis(result.structuredContent);
    if (isPositionAnalysis(normalized)) return normalized;
    throw invalidStructuredContent(result, "analyze_position");
  }

  async undo(gameId: string, version: number): Promise<GameState> {
    return this.#gameTool("undo_white_move", { game_id: gameId, version });
  }

  async #gameTool(
    tool: string,
    arguments_: Record<string, unknown>,
  ): Promise<GameState> {
    await this.#connected;
    const result = await this.#client.callTool(tool, arguments_);
    if (result.isError) throw toolError(result, tool);
    const game = gameStateFromStructuredContent(result.structuredContent);
    if (game) return game;
    throw invalidStructuredContent(result, tool);
  }

  async anchorBoard(): Promise<HostDisplayMode> {
    await this.#connected;
    const mode = await this.#client.requestPictureInPicture();
    this.#setDisplayMode(mode);
    return mode;
  }

  getDisplayMode(): HostDisplayMode {
    return this.#displayMode;
  }

  onDisplayModeChange(listener: (mode: HostDisplayMode) => void): () => void {
    this.#displayModeListeners.add(listener);
    return () => this.#displayModeListeners.delete(listener);
  }

  async requestAdvice(): Promise<void> {
    await this.#connected;
    if (!this.#client.canSendMessage()) return;
    await this.#client.sendMessage("Explain the best line for the current game.");
  }

  #setDisplayMode(mode: HostDisplayMode): void {
    if (this.#displayMode === mode) return;
    this.#displayMode = mode;
    this.#displayModeListeners.forEach((listener) => listener(mode));
  }
}

class StandardAppsClient implements AppsClient {
  readonly #app = new App(
    { name: "StockfishGPT", version: "1.0.0" },
    { availableDisplayModes: ["inline", "pip"] },
    { strict: true },
  );
  readonly #displayModeListeners = new Set<(mode: HostDisplayMode) => void>();

  constructor() {
    this.#app.addEventListener("hostcontextchanged", (context) => {
      if (context.displayMode) this.#emitDisplayMode(context.displayMode);
    });
  }

  async connect(): Promise<void> {
    await this.#app.connect();
    this.#emitDisplayMode(this.#app.getHostContext()?.displayMode ?? "inline");
  }

  onToolResult(listener: (result: ToolResult) => void): void {
    this.#app.addEventListener("toolresult", listener);
  }

  onDisplayModeChange(listener: (mode: HostDisplayMode) => void): () => void {
    this.#displayModeListeners.add(listener);
    return () => this.#displayModeListeners.delete(listener);
  }

  callTool(name: string, arguments_: Record<string, unknown>): Promise<ToolResult> {
    return this.#app.callServerTool({ name, arguments: arguments_ });
  }

  canSendMessage(): boolean {
    return Boolean(this.#app.getHostCapabilities()?.message);
  }

  async requestPictureInPicture(): Promise<HostDisplayMode> {
    const context = this.#app.getHostContext();
    const currentMode = context?.displayMode ?? "inline";
    if (currentMode === "pip") return currentMode;
    if (!context?.availableDisplayModes?.includes("pip")) return currentMode;
    const result = await this.#app.requestDisplayMode({ mode: "pip" });
    this.#emitDisplayMode(result.mode);
    return result.mode;
  }

  async sendMessage(prompt: string): Promise<void> {
    const result = await this.#app.sendMessage({
      role: "user",
      content: [{ type: "text", text: prompt }],
    });
    if (result.isError) throw new Error("The host rejected the chess follow-up.");
  }

  #emitDisplayMode(mode: HostDisplayMode): void {
    this.#displayModeListeners.forEach((listener) => listener(mode));
  }
}

function gameStateFromStructuredContent(value: unknown): GameState | null {
  const normalized = normalizeGameState(value);
  return isGameState(normalized) ? normalized : null;
}

function normalizeGameState(value: unknown): unknown {
  if (!isRecord(value)) return value;
  return { ...value, outlook: value.outlook ?? null };
}

function normalizePositionAnalysis(value: unknown): unknown {
  if (!isRecord(value)) return value;
  const candidates = Array.isArray(value.candidates)
    ? value.candidates.map(normalizeMove)
    : value.candidates;
  return {
    ...value,
    game: normalizeGameState(value.game),
    candidates,
  };
}

function normalizeMove(value: unknown): unknown {
  if (!isRecord(value)) return value;
  return {
    ...value,
    centipawns: value.centipawns ?? null,
    mate_in: value.mate_in ?? null,
  };
}

function invalidStructuredContent(result: ToolResult, tool: string): McpToolError {
  return toolError(result, tool, `${tool} returned invalid structured content.`);
}

function toolError(
  result: ToolResult,
  tool: string,
  fallback = `${tool} returned no structured content.`,
): McpToolError {
  const detail = result.content
    ?.filter((item) => item.type === "text")
    .map((item) => item.text)
    .filter(Boolean)
    .join(" ");
  console.error("MCP tool returned an error.", { tool, result });
  return new McpToolError(tool, detail || fallback);
}
