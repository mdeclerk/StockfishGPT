import { Chess, type Move as PlayedMove } from "chess.js";

import type { ChessBridge } from "./bridge";
import type {
  Difficulty,
  GameState,
  GameStatus,
  Move,
  PositionAnalysis,
  WinDrawLoss,
} from "./types";

const PIECE_VALUES = { p: 1, n: 3, b: 3, r: 5, q: 9, k: 0 } as const;

/** A browser-local stand-in for standalone widget development. */
export class LocalChessBridge implements ChessBridge {
  readonly #gameId = "local-game";
  #history: string[] = [];
  #difficulty: Difficulty = "club";
  #version = 0;
  #outlooks: (WinDrawLoss | null)[] = [null];

  async open(): Promise<GameState> {
    return this.#state();
  }

  async getGameState(gameId: string): Promise<GameState> {
    this.#requireGame(gameId);
    return this.#state();
  }

  async resetGame(
    gameId: string,
    version: number,
    difficulty: Difficulty,
  ): Promise<GameState> {
    this.#require(gameId, version);
    this.#history = [];
    this.#outlooks = [null];
    this.#difficulty = difficulty;
    this.#version += 1;
    return this.#state();
  }

  async playMove(
    gameId: string,
    version: number,
    moveUci: string,
  ): Promise<GameState> {
    this.#require(gameId, version);
    const position = chessFrom(this.#history);
    const played = playUci(position, moveUci);
    if (!played) throw new Error(`The move ${moveUci} is not legal in this game.`);
    this.#history.push(moveUci);
    this.#outlooks.push(this.#outlooks.at(-1) ?? null);

    if (statusOf(position) === "in_progress") {
      const candidates = evaluate(position, Number.POSITIVE_INFINITY);
      const reply = this.#difficulty === "beginner"
        ? candidates[candidates.length - 1]
        : candidates[0];
      playUci(position, reply.move_uci);
      this.#history.push(reply.move_uci);
      this.#outlooks.push(reply.wdl);
    }
    this.#version += 1;
    return this.#state();
  }

  async analyzePosition(gameId: string): Promise<PositionAnalysis> {
    this.#requireGame(gameId);
    const position = chessFrom(this.#history);
    return { game: this.#state(), candidates: evaluate(position, 3, true) };
  }

  async undo(gameId: string, version: number): Promise<GameState> {
    this.#require(gameId, version);
    if (!this.#history.length) throw new Error("There are no moves to undo in this game.");
    const plies = this.#history.length % 2 === 0 ? 2 : 1;
    this.#history.splice(-plies);
    this.#outlooks.splice(-plies);
    this.#version += 1;
    return this.#state();
  }

  #state(): GameState {
    const chess = chessFrom(this.#history);
    return {
      game_id: this.#gameId,
      version: this.#version,
      difficulty: this.#difficulty,
      fen: chess.fen(),
      side_to_move: chess.turn() === "w" ? "white" : "black",
      status: statusOf(chess),
      is_in_check: chess.inCheck(),
      legal_moves: chess.moves({ verbose: true }).map(uciOf),
      uci_history: [...this.#history],
      san_history: chess.history(),
      ply_count: this.#history.length,
      outlook: this.#outlooks.at(-1) ?? null,
    };
  }

  #require(gameId: string, version: number): void {
    this.#requireGame(gameId);
    if (version !== this.#version) throw new Error("The chess game changed.");
  }

  #requireGame(gameId: string): void {
    if (gameId !== this.#gameId) throw new Error("The chess game was not found.");
  }
}

function chessFrom(history: string[]): Chess {
  const chess = new Chess();
  history.forEach((uci) => playUci(chess, uci));
  return chess;
}

/** Score legal moves by material and include one expected reply for advice. */
function evaluate(position: Chess, limit: number, includeReply = false): Move[] {
  const fen = position.fen();
  const mover = position.turn();
  const scored = position.moves({ verbose: true }).map((move) => {
    const replay = new Chess(fen);
    const played = replay.move(move);
    const candidate = moveFor(replay, played);
    if (includeReply && !replay.isGameOver()) {
      const replies = evaluate(replay, 1);
      if (replies[0]) {
        candidate.principal_variation.push({
          move_uci: replies[0].move_uci,
          move_san: replies[0].move_san,
        });
      }
    }
    return { candidate, played };
  });

  scored.sort((a, b) => {
    const advantage = (candidate: Move) => mover === "w"
      ? candidate.wdl.white_expectation
      : 1 - candidate.wdl.white_expectation;
    return advantage(b.candidate) - advantage(a.candidate);
  });
  return scored.slice(0, limit).map(({ candidate }) => candidate);
}

function moveFor(position: Chess, played: PlayedMove): Move {
  const score = materialScore(position);
  const moveUci = uciOf(played);
  return {
    move_uci: moveUci,
    move_san: played.san,
    centipawns: score * 100,
    mate_in: null,
    wdl: wdlFor(score),
    principal_variation: [{ move_uci: moveUci, move_san: played.san }],
  };
}

function materialScore(position: Chess): number {
  return position.board().flat().reduce((score, piece) => {
    if (!piece) return score;
    const value = PIECE_VALUES[piece.type];
    return score + (piece.color === "w" ? value : -value);
  }, 0);
}

function wdlFor(score: number): WinDrawLoss {
  const expectation = 1 / (1 + Math.exp(-score / 4));
  const draws = Math.round(200 * (1 - Math.abs(expectation - 0.5) * 2));
  const whiteWins = Math.round(expectation * 1000 - draws / 2);
  const blackWins = 1000 - draws - whiteWins;
  return {
    white_wins: whiteWins,
    draws,
    black_wins: blackWins,
    white_expectation: (whiteWins + draws / 2) / 1000,
  };
}

function playUci(chess: Chess, uci: string): PlayedMove | null {
  try {
    return chess.move({
      from: uci.slice(0, 2),
      to: uci.slice(2, 4),
      promotion: uci.slice(4) || undefined,
    });
  } catch {
    return null;
  }
}

function uciOf(move: PlayedMove): string {
  return `${move.from}${move.to}${move.promotion ?? ""}`;
}

function statusOf(chess: Chess): GameStatus {
  if (chess.isCheckmate()) {
    return chess.turn() === "w" ? "black_wins_checkmate" : "white_wins_checkmate";
  }
  if (chess.isStalemate()) return "draw_stalemate";
  if (chess.isInsufficientMaterial()) return "draw_insufficient_material";
  if (chess.isDrawByFiftyMoves()) return "draw_fifty_move_rule";
  if (chess.isThreefoldRepetition()) return "draw_threefold_repetition";
  return "in_progress";
}
