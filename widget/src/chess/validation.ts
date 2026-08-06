import {
  DIFFICULTIES,
  GAME_STATUSES,
  SIDES,
  type GameState,
  type Move,
  type PositionAnalysis,
  type VariationMove,
  type WinDrawLoss,
} from "./types";

export function isGameState(value: unknown): value is GameState {
  return (
    isRecord(value) &&
    typeof value.game_id === "string" &&
    value.game_id.length > 0 &&
    isNonNegativeInteger(value.version) &&
    typeof value.difficulty === "string" &&
    DIFFICULTIES.includes(value.difficulty as never) &&
    typeof value.fen === "string" &&
    typeof value.side_to_move === "string" &&
    SIDES.includes(value.side_to_move as never) &&
    typeof value.status === "string" &&
    GAME_STATUSES.includes(value.status as never) &&
    typeof value.is_in_check === "boolean" &&
    isStringArray(value.legal_moves) &&
    isStringArray(value.uci_history) &&
    isStringArray(value.san_history) &&
    value.uci_history.length === value.san_history.length &&
    isNonNegativeInteger(value.ply_count) &&
    value.ply_count === value.uci_history.length &&
    isNullable(value.outlook, isWinDrawLoss)
  );
}

export function isPositionAnalysis(
  value: unknown,
): value is PositionAnalysis {
  return (
    isRecord(value) &&
    isGameState(value.game) &&
    Array.isArray(value.candidates) &&
    value.candidates.every(isMove)
  );
}

export function isMove(value: unknown): value is Move {
  return (
    isRecord(value) &&
    typeof value.move_uci === "string" &&
    typeof value.move_san === "string" &&
    isNullable(value.centipawns, isInteger) &&
    isNullable(value.mate_in, isInteger) &&
    isWinDrawLoss(value.wdl) &&
    Array.isArray(value.principal_variation) &&
    value.principal_variation.every(isVariationMove)
  );
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isVariationMove(value: unknown): value is VariationMove {
  return (
    isRecord(value) &&
    typeof value.move_uci === "string" &&
    typeof value.move_san === "string"
  );
}

function isWinDrawLoss(value: unknown): value is WinDrawLoss {
  if (!isRecord(value)) return false;
  if (
    !isNonNegativeInteger(value.white_wins) ||
    !isNonNegativeInteger(value.draws) ||
    !isNonNegativeInteger(value.black_wins)
  ) {
    return false;
  }
  return (
    value.white_wins + value.draws + value.black_wins > 0 &&
    isProbability(value.white_expectation)
  );
}

function isNullable<T>(
  value: unknown,
  guard: (candidate: unknown) => candidate is T,
): value is T | null {
  return value === null || guard(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return isInteger(value) && value >= 0;
}

function isInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value);
}

function isProbability(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= 1
  );
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}
