import { describe, expect, it, vi } from "vitest";

import type { HostDisplayMode } from "./bridge";
import { McpChessBridge, type AppsClient } from "./mcpBridge";
import { DIFFICULTIES, STARTING_FEN, type GameState, type Move } from "./types";

class FakeAppsClient implements AppsClient {
  readonly connect = vi.fn(async () => undefined);
  readonly callTool = vi.fn(
    async (_name: string, _arguments: Record<string, unknown>) =>
      this.results.shift() ?? { isError: true },
  );
  readonly sendMessage = vi.fn(async (_prompt: string) => undefined);
  readonly requestPictureInPicture = vi.fn(
    async (): Promise<HostDisplayMode> => "pip",
  );
  readonly results: {
    structuredContent?: unknown;
    content?: { type: string; text?: string }[];
    isError?: boolean;
  }[] = [];
  listener: ((result: { structuredContent?: unknown; isError?: boolean }) => void) | null = null;
  displayModeListener: ((mode: HostDisplayMode) => void) | null = null;
  messagesEnabled = true;

  onToolResult(
    listener: (result: { structuredContent?: unknown; isError?: boolean }) => void,
  ): void {
    this.listener = listener;
  }

  onDisplayModeChange(listener: (mode: HostDisplayMode) => void): () => void {
    this.displayModeListener = listener;
    return () => {
      if (this.displayModeListener === listener) this.displayModeListener = null;
    };
  }

  canSendMessage(): boolean {
    return this.messagesEnabled;
  }

  emitStartedGame(result: unknown): void {
    this.listener?.({ structuredContent: result });
  }

  emitDisplayMode(mode: HostDisplayMode): void {
    this.displayModeListener?.(mode);
  }
}

describe("McpChessBridge", () => {
  it("uses canonical runtime difficulty values", () => {
    expect(DIFFICULTIES).toEqual(["beginner", "club", "strong"]);
  });

  it("hydrates the original game ID from current server state", async () => {
    const client = new FakeAppsClient();
    const bridge = new McpChessBridge(client);
    const current = gameState({
      version: 3,
      uci_history: ["e2e4", "e7e5"],
      san_history: ["e4", "e5"],
      ply_count: 2,
    });
    client.results.push({ structuredContent: current });
    const opened = bridge.open();

    client.emitStartedGame(gameState());

    await expect(opened).resolves.toEqual(current);
    expect(client.callTool).toHaveBeenCalledWith("get_game_state", {
      game_id: "game-1",
    });
  });

  it("calls every mutation with game identity and version", async () => {
    const client = new FakeAppsClient();
    const bridge = new McpChessBridge(client);
    const next = gameState({ version: 5 });
    client.results.push(
      { structuredContent: next },
      { structuredContent: next },
      { structuredContent: next },
    );

    await bridge.resetGame("game-1", 4, "strong");
    expect(client.callTool).toHaveBeenLastCalledWith("reset_game", {
      game_id: "game-1",
      version: 4,
      difficulty: "strong",
    });
    await bridge.playMove("game-1", 4, "e2e4");
    expect(client.callTool).toHaveBeenLastCalledWith("play_white_move", {
      game_id: "game-1",
      version: 4,
      move_uci: "e2e4",
    });
    await bridge.undo("game-1", 4);
    expect(client.callTool).toHaveBeenLastCalledWith("undo_white_move", {
      game_id: "game-1",
      version: 4,
    });
  });

  it("returns principal-variation UCI and SAN for advice", async () => {
    const client = new FakeAppsClient();
    const bridge = new McpChessBridge(client);
    const move = candidate();
    client.results.push({
      structuredContent: { game: gameState(), candidates: [move] },
    });

    await expect(bridge.analyzePosition("game-1")).resolves.toEqual({
      game: gameState(),
      candidates: [move],
    });
    expect(client.callTool).toHaveBeenLastCalledWith("analyze_position", {
      game_id: "game-1",
    });
  });

  it("restores nullable fields omitted by a host", async () => {
    const client = new FakeAppsClient();
    const bridge = new McpChessBridge(client);
    const { outlook: _outlook, ...withoutOutlook } = gameState();
    const move = candidate();
    const { centipawns: _centipawns, mate_in: _mate, ...withoutScores } = move;
    client.results.push({
      structuredContent: {
        game: withoutOutlook,
        candidates: [withoutScores],
      },
    });

    const result = await bridge.analyzePosition("game-1");

    expect(result.game.outlook).toBeNull();
    expect(result.candidates[0]).toMatchObject({ centipawns: null, mate_in: null });
  });

  it("rejects malformed structured content", async () => {
    const client = new FakeAppsClient();
    const bridge = new McpChessBridge(client);
    client.results.push({ structuredContent: { game_id: "game-1" } });

    await expect(bridge.getGameState("game-1")).rejects.toMatchObject({
      name: "McpToolError",
      tool: "get_game_state",
      message: "get_game_state returned invalid structured content.",
    });
  });

  it("surfaces exact MCP tool errors", async () => {
    const client = new FakeAppsClient();
    const bridge = new McpChessBridge(client);
    client.results.push({
      isError: true,
      content: [{ type: "text", text: "The chess game changed." }],
    });

    await expect(bridge.undo("game-1", 0)).rejects.toMatchObject({
      tool: "undo_white_move",
      message: "The chess game changed.",
    });
  });

  it("sends concise advice for the authoritative current game", async () => {
    const client = new FakeAppsClient();
    const bridge = new McpChessBridge(client);

    await bridge.requestAdvice();

    expect(client.sendMessage).toHaveBeenCalledWith(
      "Explain the best line for the current game.",
    );
  });

  it("treats unavailable messaging as nonfatal", async () => {
    const client = new FakeAppsClient();
    client.messagesEnabled = false;
    const bridge = new McpChessBridge(client);

    await expect(bridge.requestAdvice()).resolves.toBeUndefined();
    expect(client.sendMessage).not.toHaveBeenCalled();
  });

  it("tracks Picture-in-Picture and host-driven mode changes", async () => {
    const client = new FakeAppsClient();
    const bridge = new McpChessBridge(client);
    const listener = vi.fn();
    bridge.onDisplayModeChange(listener);

    await expect(bridge.anchorBoard()).resolves.toBe("pip");
    client.emitDisplayMode("inline");

    expect(listener).toHaveBeenNthCalledWith(1, "pip");
    expect(listener).toHaveBeenNthCalledWith(2, "inline");
  });
});

function gameState(overrides: Partial<GameState> = {}): GameState {
  return {
    game_id: "game-1",
    version: 0,
    difficulty: "club",
    fen: STARTING_FEN,
    side_to_move: "white",
    status: "in_progress",
    is_in_check: false,
    legal_moves: ["e2e4", "g1f3"],
    uci_history: [],
    san_history: [],
    ply_count: 0,
    outlook: null,
    ...overrides,
  };
}

function candidate(): Move {
  return {
    move_uci: "e2e4",
    move_san: "e4",
    centipawns: 25,
    mate_in: null,
    wdl: {
      white_wins: 440,
      draws: 400,
      black_wins: 160,
      white_expectation: 0.64,
    },
    principal_variation: [
      { move_uci: "e2e4", move_san: "e4" },
      { move_uci: "e7e5", move_san: "e5" },
    ],
  };
}
