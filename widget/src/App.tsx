import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chessboard } from "react-chessboard";

import type { ChessBridge, HostBridge, HostDisplayMode } from "./chess/bridge";
import { McpToolError } from "./chess/mcpBridge";
import {
  STARTING_FEN,
  type Difficulty,
  type GameState,
  type GameStatus,
  type PositionAnalysis,
  type VariationMove,
  type WinDrawLoss,
} from "./chess/types";

type PendingAction = "opening" | "move" | "advice" | "undo" | null;
type Promotion = { from: string; to: string };
type Issue = { tone: "error" | "warning"; message: string; detail?: string };
const NARROW_LAYOUT_QUERY = "(max-width: 680px)";
const COARSE_POINTER_QUERY = "(pointer: coarse)";
const SUGGESTION_COLOR = "rgb(96 130 67 / 78%)";
const REPLY_COLOR = "rgb(190 132 48 / 82%)";

interface AppProps {
  bridge: ChessBridge;
  host?: HostBridge;
}

const OUTCOMES: Record<Exclude<GameStatus, "in_progress">, string> = {
  white_wins_checkmate: "Checkmate · You win",
  black_wins_checkmate: "Checkmate · Black wins",
  draw_stalemate: "Draw · Stalemate",
  draw_insufficient_material: "Draw · Insufficient material",
  draw_fifty_move_rule: "Draw · Fifty-move rule",
  draw_threefold_repetition: "Draw · Threefold repetition",
};

export function App({ bridge, host }: AppProps) {
  const [game, setGame] = useState<GameState | null>(null);
  const [analysis, setAnalysis] = useState<PositionAnalysis | null>(null);
  const [pending, setPending] = useState<PendingAction>("opening");
  const [issue, setIssue] = useState<Issue | null>(null);
  const [promotion, setPromotion] = useState<Promotion | null>(null);
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
  const [newGameMenuOpen, setNewGameMenuOpen] = useState(false);
  const [displayMode, setDisplayMode] = useState<HostDisplayMode>(
    () => host?.getDisplayMode() ?? "inline",
  );
  const isNarrowLayout = useMediaQuery(NARROW_LAYOUT_QUERY);
  const hasCoarsePointer = useMediaQuery(COARSE_POINTER_QUERY);
  const usesTapInput = isNarrowLayout || hasCoarsePointer;
  const started = useRef(false);
  const anchorAttempted = useRef(false);
  const newGameMenu = useRef<HTMLDivElement>(null);
  const ready = game !== null;
  const best = analysis?.candidates[0] ?? null;
  const bestLine = best?.principal_variation.slice(0, 2) ?? [];
  const outlook = best?.wdl ?? game?.outlook ?? null;

  const acceptGame = useCallback((next: GameState) => {
    setGame(next);
    setAnalysis(null);
    setPromotion(null);
    setSelectedSquare(null);
  }, []);

  const openGame = useCallback(async () => {
    setPending("opening");
    setIssue(null);
    try {
      acceptGame(await bridge.open());
    } catch (cause) {
      console.error("Could not open StockfishGPT.", cause);
      setIssue(issueFrom(cause));
    } finally {
      setPending(null);
    }
  }, [acceptGame, bridge]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void openGame();
  }, [openGame]);

  useEffect(() => host?.onDisplayModeChange(setDisplayMode), [host]);

  useEffect(() => {
    if (!newGameMenuOpen) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!newGameMenu.current?.contains(event.target as Node)) {
        setNewGameMenuOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setNewGameMenuOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [newGameMenuOpen]);

  useEffect(() => {
    if (!ready || anchorAttempted.current || !host) return;
    anchorAttempted.current = true;
    void host
      .anchorBoard()
      .then(setDisplayMode)
      .catch((cause) => {
        console.warn("Could not keep the board anchored in ChatGPT.", cause);
        setIssue(
          warningFrom(cause, "The board will remain inline in the conversation."),
        );
      });
  }, [host, ready]);

  const recoverAfter = useCallback(
    async (cause: unknown, current: GameState) => {
      try {
        acceptGame(await bridge.getGameState(current.game_id));
      } catch (refreshCause) {
        console.warn("Could not refresh the chess game after an error.", refreshCause);
      }
      setIssue(issueFrom(cause));
    },
    [acceptGame, bridge],
  );

  const submitMove = useCallback(
    async (moveUci: string) => {
      if (!game || pending || game.side_to_move !== "white") return;
      setPending("move");
      setIssue(null);
      setPromotion(null);
      setSelectedSquare(null);
      try {
        acceptGame(await bridge.playMove(game.game_id, game.version, moveUci));
      } catch (cause) {
        console.error("Could not play the move.", cause);
        await recoverAfter(cause, game);
      } finally {
        setPending(null);
      }
    },
    [acceptGame, bridge, game, pending, recoverAfter],
  );

  const tryMove = useCallback(
    (from: string, to: string): boolean => {
      if (!game || pending || game.side_to_move !== "white") return false;
      const moves = movesFrom(game, from).filter((uci) => uci.slice(2, 4) === to);
      if (!moves.length) return false;
      if (moves.some((uci) => uci.length > 4)) {
        setPromotion({ from, to });
        return false;
      }
      void submitMove(`${from}${to}`);
      return true;
    },
    [game, pending, submitMove],
  );

  const squareStyles = useMemo(() => {
    const styles: Record<string, React.CSSProperties> = {};
    if (!game || !selectedSquare) return styles;
    styles[selectedSquare] = { boxShadow: "inset 0 0 0 4px #e5c07b" };
    movesFrom(game, selectedSquare).forEach((uci) => {
      styles[uci.slice(2, 4)] = {
        background: "radial-gradient(circle, #4d613c 18%, transparent 20%)",
      };
    });
    return styles;
  }, [game, selectedSquare]);

  const resetGame = async (difficulty: Difficulty) => {
    if (!game || pending) return;
    setPending("opening");
    setIssue(null);
    try {
      acceptGame(
        await bridge.resetGame(game.game_id, game.version, difficulty),
      );
    } catch (cause) {
      console.error("Could not reset the game.", cause);
      await recoverAfter(cause, game);
    } finally {
      setPending(null);
    }
  };

  const askForAdvice = async () => {
    if (!game || pending || game.status !== "in_progress") return;
    setPending("advice");
    setIssue(null);
    try {
      const next = await bridge.analyzePosition(game.game_id);
      setGame(next.game);
      setAnalysis(next);
      if (next.candidates[0] && host) {
        const warning = await followUpWarning(
          host.requestAdvice(),
          "move advice",
        );
        if (warning) setIssue(warning);
      }
    } catch (cause) {
      console.error("Could not analyze the position.", cause);
      setIssue(issueFrom(cause));
    } finally {
      setPending(null);
    }
  };

  const undoLastTurn = async () => {
    if (!game || pending || game.ply_count === 0) return;
    setPending("undo");
    setIssue(null);
    try {
      acceptGame(await bridge.undo(game.game_id, game.version));
    } catch (cause) {
      console.error("Could not undo the turn.", cause);
      await recoverAfter(cause, game);
    } finally {
      setPending(null);
    }
  };

  const status = statusText(game, pending);
  const surfaceClassName = [
    "game-surface",
    displayMode === "pip" ? "game-surface--pip" : "",
    issue ? "game-surface--has-issue" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <main className={surfaceClassName} data-display-mode={displayMode}>
      <section className="game-layout" aria-label="Chess game">
        <WdlBar outlook={outlook} />
        <div className="board-wrap" aria-busy={Boolean(pending)}>
          <Chessboard
            options={{
              id: "coach-board",
              position: game?.fen ?? STARTING_FEN,
              boardOrientation: "white",
              showNotation: true,
              allowDrawingArrows: true,
              allowDragging:
                ready &&
                !usesTapInput &&
                !pending &&
                game.status === "in_progress" &&
                game.side_to_move === "white",
              canDragPiece: ({ piece }) => piece.pieceType.startsWith("w"),
              onPieceDrop: ({ sourceSquare, targetSquare }) =>
                Boolean(targetSquare && tryMove(sourceSquare, targetSquare)),
              onSquareClick: ({ square }) => {
                if (selectedSquare && tryMove(selectedSquare, square)) return;
                setSelectedSquare(
                  game && movesFrom(game, square).length ? square : null,
                );
              },
              squareStyles,
              arrows: adviceArrows(bestLine),
              lightSquareStyle: { backgroundColor: "#f0d9b5" },
              darkSquareStyle: { backgroundColor: "#b58863" },
              lightSquareNotationStyle: { color: "#8b684c" },
              darkSquareNotationStyle: { color: "#f0d9b5" },
              boardStyle: { borderRadius: "6px", overflow: "hidden" },
            }}
          />
          {pending === "opening" && <BoardOverlay label="Preparing the board…" />}
          {!ready && pending !== "opening" && issue?.tone === "error" && (
            <OpeningFailure issue={issue} onRetry={() => void openGame()} />
          )}
          {pending === "move" && <BoardOverlay label="Black is thinking…" />}
          {ready && game.status !== "in_progress" && (
            <div className="result-overlay" role="status">
              {OUTCOMES[game.status]}
            </div>
          )}
        </div>

        <aside className="toolbar-column" aria-label="Game controls">
          <div className="mini-toolbar">
            <div className="new-game-control" ref={newGameMenu}>
              <ToolbarButton
                label="New game"
                disabled={!ready || Boolean(pending)}
                expanded={newGameMenuOpen}
                onClick={() => setNewGameMenuOpen((open) => !open)}
              >
                <CirclePlusIcon />
              </ToolbarButton>
              {newGameMenuOpen && game && (
                <DifficultyMenu
                  selected={game.difficulty}
                  onSelect={(difficulty) => {
                    setNewGameMenuOpen(false);
                    void resetGame(difficulty);
                  }}
                />
              )}
            </div>
            <ToolbarButton
              label="Undo"
              disabled={!ready || Boolean(pending) || game.ply_count === 0}
              onClick={() => void undoLastTurn()}
            >
              <UndoIcon />
            </ToolbarButton>
            <ToolbarButton
              label="Advice"
              primary
              loading={pending === "advice"}
              disabled={
                !ready || Boolean(pending) || game.status !== "in_progress"
              }
              onClick={() => void askForAdvice()}
            >
              <SparklesIcon />
            </ToolbarButton>
          </div>
        </aside>
        {ready && issue && <IssueStrip issue={issue} />}
      </section>
      <p className="visually-hidden" role="status" aria-live="polite">
        {status}
      </p>
      {bestLine.length > 0 && (
        <p className="visually-hidden" role="status">
          {adviceText(bestLine)}
        </p>
      )}

      {promotion && (
        <PromotionDialog
          onCancel={() => setPromotion(null)}
          onSelect={(piece) =>
            void submitMove(`${promotion.from}${promotion.to}${piece}`)
          }
        />
      )}
    </main>
  );
}

function movesFrom(game: GameState, square: string): string[] {
  return game.legal_moves.filter((uci) => uci.startsWith(square));
}

function adviceArrows(line: VariationMove[]) {
  return line.map((move, index) => ({
    startSquare: move.move_uci.slice(0, 2),
    endSquare: move.move_uci.slice(2, 4),
    color: index === 0 ? SUGGESTION_COLOR : REPLY_COLOR,
  }));
}

function adviceText(line: VariationMove[]): string {
  const [suggestion, reply] = line;
  return reply
    ? `Suggested move: ${suggestion.move_san}. Black's expected reply: ${reply.move_san}.`
    : `Suggested move: ${suggestion.move_san}.`;
}

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(
    () => window.matchMedia?.(query).matches ?? false,
  );
  useEffect(() => {
    if (!window.matchMedia) return;
    const mediaQuery = window.matchMedia(query);
    const updateMatch = () => setMatches(mediaQuery.matches);
    updateMatch();
    mediaQuery.addEventListener("change", updateMatch);
    return () => mediaQuery.removeEventListener("change", updateMatch);
  }, [query]);
  return matches;
}

function OpeningFailure({ issue, onRetry }: { issue: Issue; onRetry: () => void }) {
  return (
    <div className="opening-failure" role="alert">
      <p>{issue.message}</p>
      <button type="button" onClick={onRetry}>Retry</button>
    </div>
  );
}

function IssueStrip({ issue }: { issue: Issue }) {
  return (
    <div
      className={`issue-strip issue-strip--${issue.tone}`}
      role={issue.tone === "error" ? "alert" : "status"}
    >
      <div>
        <strong>{issue.tone === "error" ? "Couldn’t complete that action" : "Board updated with a warning"}</strong>
        <p>{issue.message}</p>
      </div>
      {issue.detail && (
        <details>
          <summary>Details</summary>
          <pre>{issue.detail}</pre>
        </details>
      )}
    </div>
  );
}

function ToolbarButton({
  children,
  disabled,
  expanded,
  label,
  loading = false,
  onClick,
  primary = false,
}: {
  children: React.ReactNode;
  disabled: boolean;
  expanded?: boolean;
  label: string;
  loading?: boolean;
  onClick: () => void;
  primary?: boolean;
}) {
  return (
    <button
      className={`tool-button${primary ? " tool-button--primary" : ""}`}
      type="button"
      aria-label={label}
      aria-busy={loading}
      aria-expanded={expanded}
      aria-haspopup={expanded === undefined ? undefined : "menu"}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      {loading ? <span className="button-spinner" aria-hidden="true" /> : children}
    </button>
  );
}

function DifficultyMenu({
  onSelect,
  selected,
}: {
  onSelect: (difficulty: Difficulty) => void;
  selected: Difficulty;
}) {
  const levels: { value: Difficulty; label: string }[] = [
    { value: "beginner", label: "Beginner" },
    { value: "club", label: "Club" },
    { value: "strong", label: "Strong" },
  ];
  return (
    <div className="difficulty-menu" role="menu" aria-label="Black strength">
      {levels.map(({ value, label }) => (
        <button
          key={value}
          type="button"
          role="menuitemradio"
          aria-checked={selected === value}
          onClick={() => onSelect(value)}
        >
          <span>{label}</span>
          {selected === value && <CheckIcon />}
        </button>
      ))}
    </div>
  );
}

function Icon({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.8"
    >
      {children}
    </svg>
  );
}

function SparklesIcon() {
  return (
    <Icon>
      <path d="m12 3 1.15 3.35L16.5 7.5l-3.35 1.15L12 12l-1.15-3.35L7.5 7.5l3.35-1.15L12 3Z" />
      <path d="m18.5 13 .72 2.08L21.3 15.8l-2.08.72L18.5 18.6l-.72-2.08-2.08-.72 2.08-.72L18.5 13Z" />
      <path d="m6 13 .9 2.6 2.6.9-2.6.9L6 20l-.9-2.6-2.6-.9 2.6-.9L6 13Z" />
    </Icon>
  );
}

function UndoIcon() {
  return <Icon><path d="M9 7 4.5 11 9 15" /><path d="M5 11h8.5a5 5 0 0 1 5 5v1" /></Icon>;
}

function CirclePlusIcon() {
  return <Icon><circle cx="12" cy="12" r="8.5" /><path d="M12 8v8M8 12h8" /></Icon>;
}

function CheckIcon() {
  return <Icon className="menu-check"><path d="m6 12 4 4 8-8" /></Icon>;
}

function WdlBar({ outlook }: { outlook: WinDrawLoss | null }) {
  const total = outlook
    ? outlook.white_wins + outlook.draws + outlook.black_wins
    : 0;
  const segments = outlook && total > 0
    ? {
        black: Math.round((outlook.black_wins / total) * 100),
        draw: Math.round((outlook.draws / total) * 100),
        white: Math.round((outlook.white_wins / total) * 100),
      }
    : { black: 25, draw: 50, white: 25 };
  return (
    <div
      className="wdl-bar"
      role="img"
      aria-label={`Engine outlook: White ${segments.white}%, Draw ${segments.draw}%, Black ${segments.black}%`}
    >
      <div className="wdl-segment wdl-segment--black" style={{ flexBasis: `${segments.black}%` }} title={`Black ${segments.black}%`}><span>B</span></div>
      <div className="wdl-segment wdl-segment--draw" style={{ flexBasis: `${segments.draw}%` }} title={`Draw ${segments.draw}%`}><span>D</span></div>
      <div className="wdl-segment wdl-segment--white" style={{ flexBasis: `${segments.white}%` }} title={`White ${segments.white}%`}><span>W</span></div>
    </div>
  );
}

function BoardOverlay({ label }: { label: string }) {
  return (
    <div className="board-overlay" role="status" aria-label={label}>
      <span className="spinner" aria-hidden="true" />
      <span className="visually-hidden">{label}</span>
    </div>
  );
}

function PromotionDialog({
  onCancel,
  onSelect,
}: {
  onCancel: () => void;
  onSelect: (piece: "q" | "r" | "b" | "n") => void;
}) {
  const pieces = [
    ["q", "♕", "Queen"],
    ["r", "♖", "Rook"],
    ["b", "♗", "Bishop"],
    ["n", "♘", "Knight"],
  ] as const;
  return (
    <div className="dialog-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="promotion-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="promotion-title"
        onClick={(event) => event.stopPropagation()}
      >
        <p id="promotion-title">Choose promotion</p>
        <div>
          {pieces.map(([value, glyph, label]) => (
            <button key={value} type="button" onClick={() => onSelect(value)}>
              <span aria-hidden="true">{glyph}</span><span>{label}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function statusText(game: GameState | null, pending: PendingAction): string {
  if (pending === "opening") return "Preparing the board…";
  if (pending === "move") return "Black is thinking…";
  if (!game) return "Board unavailable";
  if (game.status !== "in_progress") return OUTCOMES[game.status];
  return game.side_to_move === "white" ? "Your turn · White" : "Black to move";
}

function issueFrom(cause: unknown): Issue {
  if (cause instanceof McpToolError) {
    return { tone: "error", message: cause.message, detail: `Tool: ${cause.tool}` };
  }
  return {
    tone: "error",
    message: cause instanceof Error ? cause.message : "Something went wrong. Try again.",
  };
}

function warningFrom(cause: unknown, message: string): Issue {
  return {
    tone: "warning",
    message,
    detail: cause instanceof Error ? cause.message : undefined,
  };
}

async function followUpWarning(
  request: Promise<void> | undefined,
  label: string,
): Promise<Issue | null> {
  try {
    await request;
    return null;
  } catch (cause) {
    console.warn(`Could not request ${label}.`, cause);
    return warningFrom(cause, `The ${label} could not be sent to ChatGPT.`);
  }
}
