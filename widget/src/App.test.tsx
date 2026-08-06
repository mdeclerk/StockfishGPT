import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Chess } from "chess.js";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { ChessBridge, HostBridge, HostDisplayMode } from "./chess/bridge";
import { McpToolError } from "./chess/mcpBridge";
import type {
  Difficulty,
  GameState,
  Move,
  PositionAnalysis,
} from "./chess/types";

interface MockBoardOptions {
  position?: string;
  showNotation?: boolean;
  allowDragging?: boolean;
  arrows?: { startSquare: string; endSquare: string; color: string }[];
  squareStyles?: Record<string, unknown>;
  onSquareClick?: (input: { square: string }) => void;
  onPieceDrop?: (input: {
    piece: { pieceType: string };
    sourceSquare: string;
    targetSquare: string;
  }) => boolean;
}

const latest = vi.hoisted(() => ({ options: null as MockBoardOptions | null }));

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: MockBoardOptions }) => {
    latest.options = options;
    return (
      <div
        aria-label="Chess board"
        data-position={options.position}
        data-draggable={options.allowDragging}
        data-arrows={(options.arrows ?? [])
          .map((arrow) => `${arrow.startSquare}-${arrow.endSquare}`)
          .join(",")}
        data-arrow-colors={(options.arrows ?? [])
          .map((arrow) => arrow.color)
          .join(",")}
        data-highlighted-squares={Object.keys(options.squareStyles ?? {}).join(",")}
      >
        {options.showNotation && <span>Coordinates a–h and 1–8</span>}
      </div>
    );
  },
}));

afterEach(() => vi.unstubAllGlobals());

const board = {
  drop(from: string, to: string): void {
    act(() => {
      latest.options?.onPieceDrop?.({
        piece: { pieceType: "wP" },
        sourceSquare: from,
        targetSquare: to,
      });
    });
  },
  click(square: string): void {
    act(() => latest.options?.onSquareClick?.({ square }));
  },
  element(): HTMLElement {
    return screen.getByLabelText("Chess board");
  },
};

class FakeBridge implements ChessBridge {
  current = state();
  readonly open = vi.fn(async () => this.current);
  readonly getGameState = vi.fn(async (_gameId: string) => this.current);
  readonly resetGame = vi.fn(
    async (gameId: string, version: number, difficulty: Difficulty) => {
      this.current = state({
        game_id: gameId,
        version: version + 1,
        difficulty,
      });
      return this.current;
    },
  );
  readonly playMove = vi.fn(
    async (gameId: string, version: number, moveUci: string) => {
      this.current = stateFromHistory([moveUci, "e7e5"], {
        game_id: gameId,
        version: version + 1,
      });
      return this.current;
    },
  );
  readonly analyzePosition = vi.fn(
    async (_gameId: string): Promise<PositionAnalysis> => ({
      game: this.current,
      candidates: [candidate()],
    }),
  );
  readonly undo = vi.fn(async (gameId: string, version: number) => {
    this.current = state({ game_id: gameId, version: version + 1 });
    return this.current;
  });
}

class FakeHost implements HostBridge {
  readonly anchorBoard = vi.fn(async (): Promise<HostDisplayMode> => "pip");
  readonly requestAdvice = vi.fn(async () => undefined);
  mode: HostDisplayMode = "inline";
  listener: ((mode: HostDisplayMode) => void) | null = null;

  getDisplayMode(): HostDisplayMode {
    return this.mode;
  }

  onDisplayModeChange(listener: (mode: HostDisplayMode) => void): () => void {
    this.listener = listener;
    return () => {
      if (this.listener === listener) this.listener = null;
    };
  }

  emitDisplayMode(mode: HostDisplayMode): void {
    this.mode = mode;
    this.listener?.(mode);
  }
}

describe("App", () => {
  it("hydrates and renders the authoritative server snapshot", async () => {
    const bridge = new FakeBridge();
    bridge.current = stateFromHistory(["e2e4", "e7e5"], { version: 4 });

    render(<App bridge={bridge} />);

    expect(await screen.findByText("Your turn · White")).toBeInTheDocument();
    expect(board.element()).toHaveAttribute("data-position", bridge.current.fen);
    expect(bridge.open).toHaveBeenCalledOnce();
  });

  it("submits only server-provided legal moves with identity and version", async () => {
    const bridge = new FakeBridge();
    bridge.current = state({ version: 7, legal_moves: ["e2e4"] });
    render(<App bridge={bridge} />);
    await screen.findByText("Your turn · White");

    board.drop("d2", "d4");
    expect(bridge.playMove).not.toHaveBeenCalled();
    board.drop("e2", "e4");

    await waitFor(() =>
      expect(bridge.playMove).toHaveBeenCalledWith("game-1", 7, "e2e4"),
    );
    expect(board.element()).toHaveAttribute("data-position", bridge.current.fen);
  });

  it("uses supplied promotion moves without calculating chess rules", async () => {
    const bridge = new FakeBridge();
    bridge.current = stateFromHistory(
      ["a2a4", "b7b5", "a4b5", "a7a6", "b5a6", "d7d6", "a6a7", "e7e6"],
      { legal_moves: ["a7b8q", "a7b8r", "a7b8b", "a7b8n"] },
    );
    bridge.playMove.mockResolvedValue({ ...bridge.current, version: 1 });
    render(<App bridge={bridge} />);
    await screen.findByText("Your turn · White");

    board.drop("a7", "b8");
    fireEvent.click(screen.getByRole("button", { name: "Queen" }));

    await waitFor(() =>
      expect(bridge.playMove).toHaveBeenCalledWith(
        "game-1",
        0,
        "a7b8q",
      ),
    );
  });

  it("resets the same game with its current version", async () => {
    const bridge = new FakeBridge();
    bridge.current = stateFromHistory(["e2e4", "e7e5"], { version: 3 });
    render(<App bridge={bridge} />);
    await screen.findByText("Your turn · White");

    fireEvent.click(screen.getByRole("button", { name: "New game" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Strong" }));

    await waitFor(() =>
      expect(bridge.resetGame).toHaveBeenCalledWith("game-1", 3, "strong"),
    );
    expect(bridge.current.game_id).toBe("game-1");
    expect(board.element()).toHaveAttribute("data-position", bridge.current.fen);

    fireEvent.click(screen.getByRole("button", { name: "New game" }));
    expect(
      screen.getByRole("menuitemradio", { name: "Strong" }),
    ).toHaveAttribute("aria-checked", "true");
  });

  it("undoes through the server and replaces the snapshot", async () => {
    const bridge = new FakeBridge();
    bridge.current = stateFromHistory(["e2e4", "e7e5"], { version: 2 });
    render(<App bridge={bridge} />);
    await screen.findByText("Your turn · White");

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));

    await waitFor(() => expect(bridge.undo).toHaveBeenCalledWith("game-1", 2));
    expect(board.element()).toHaveAttribute("data-position", bridge.current.fen);
  });

  it("displays White's suggestion and Black's reply and sends concise advice", async () => {
    const bridge = new FakeBridge();
    const host = new FakeHost();
    render(<App bridge={bridge} host={host} />);
    await screen.findByText("Your turn · White");

    fireEvent.click(screen.getByRole("button", { name: "Advice" }));

    await waitFor(() =>
      expect(bridge.analyzePosition).toHaveBeenCalledWith("game-1"),
    );
    expect(board.element()).toHaveAttribute("data-arrows", "e2-e4,e7-e5");
    expect(board.element().getAttribute("data-arrow-colors")).toContain("96 130 67");
    expect(board.element().getAttribute("data-arrow-colors")).toContain("190 132 48");
    expect(
      screen.getByText("Suggested move: e4. Black's expected reply: e5."),
    ).toBeInTheDocument();
    expect(host.requestAdvice).toHaveBeenCalledOnce();
  });

  it("shows only the suggestion when no Black reply is available", async () => {
    const bridge = new FakeBridge();
    const oneMove = candidate();
    oneMove.principal_variation = oneMove.principal_variation.slice(0, 1);
    bridge.analyzePosition.mockResolvedValue({
      game: bridge.current,
      candidates: [oneMove],
    });
    render(<App bridge={bridge} />);
    await screen.findByText("Your turn · White");

    fireEvent.click(screen.getByRole("button", { name: "Advice" }));

    await waitFor(() => expect(board.element()).toHaveAttribute("data-arrows", "e2-e4"));
    expect(screen.getByText("Suggested move: e4.")).toBeInTheDocument();
  });

  it("refreshes authoritative state while preserving the exact mutation error", async () => {
    const bridge = new FakeBridge();
    bridge.playMove.mockRejectedValue(
      new McpToolError("play_white_move", "Chess game changed: current version is 2."),
    );
    bridge.current = state({ version: 2 });
    render(<App bridge={bridge} />);
    await screen.findByText("Your turn · White");

    board.drop("e2", "e4");

    expect(
      await screen.findByText("Chess game changed: current version is 2."),
    ).toBeInTheDocument();
    expect(bridge.getGameState).toHaveBeenCalledWith("game-1");
  });

  it("keeps advice message and Picture-in-Picture failures nonfatal", async () => {
    const bridge = new FakeBridge();
    const host = new FakeHost();
    host.anchorBoard.mockRejectedValue(new Error("PiP unavailable"));
    host.requestAdvice.mockRejectedValue(new Error("Message unavailable"));
    render(<App bridge={bridge} host={host} />);

    expect(
      await screen.findByText("The board will remain inline in the conversation."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Advice" }));
    expect(
      await screen.findByText("The move advice could not be sent to ChatGPT."),
    ).toBeInTheDocument();
    expect(board.element()).toBeInTheDocument();
  });

  it("tracks host-driven display mode changes", async () => {
    const bridge = new FakeBridge();
    const host = new FakeHost();
    render(<App bridge={bridge} host={host} />);

    await waitFor(() => expect(screen.getByRole("main")).toHaveClass("game-surface--pip"));
    act(() => host.emitDisplayMode("inline"));
    expect(screen.getByRole("main")).not.toHaveClass("game-surface--pip");
  });
});

function state(overrides: Partial<GameState> = {}): GameState {
  const chess = new Chess();
  return {
    game_id: "game-1",
    version: 0,
    difficulty: "club",
    fen: chess.fen(),
    side_to_move: "white",
    status: "in_progress",
    is_in_check: false,
    legal_moves: chess.moves({ verbose: true }).map(uciOf),
    uci_history: [],
    san_history: [],
    ply_count: 0,
    outlook: null,
    ...overrides,
  };
}

function stateFromHistory(
  history: string[],
  overrides: Partial<GameState> = {},
): GameState {
  const chess = new Chess();
  history.forEach((uci) => {
    chess.move({
      from: uci.slice(0, 2),
      to: uci.slice(2, 4),
      promotion: uci.slice(4) || undefined,
    });
  });
  return state({
    fen: chess.fen(),
    side_to_move: chess.turn() === "w" ? "white" : "black",
    legal_moves: chess.moves({ verbose: true }).map(uciOf),
    uci_history: history,
    san_history: chess.history(),
    ply_count: history.length,
    ...overrides,
  });
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

function uciOf(move: { from: string; to: string; promotion?: string }): string {
  return `${move.from}${move.to}${move.promotion ?? ""}`;
}
