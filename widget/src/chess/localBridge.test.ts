import { describe, expect, it } from "vitest";

import { LocalChessBridge } from "./localBridge";

describe("LocalChessBridge", () => {
  it("exposes the same authoritative lifecycle as the MCP bridge", async () => {
    const bridge = new LocalChessBridge();
    const opened = await bridge.open();

    const played = await bridge.playMove(
      opened.game_id,
      opened.version,
      "e2e4",
    );

    expect(played.uci_history).toHaveLength(2);
    expect(played.side_to_move).toBe("white");
    expect(played.version).toBe(1);

    const undone = await bridge.undo(played.game_id, played.version);
    expect(undone.uci_history).toEqual([]);
    expect(undone.version).toBe(2);
  });

  it("returns a suggested move and Black reply for advice", async () => {
    const bridge = new LocalChessBridge();
    const game = await bridge.open();

    const analysis = await bridge.analyzePosition(game.game_id);

    expect(analysis.candidates).toHaveLength(3);
    expect(analysis.candidates[0].principal_variation).toHaveLength(2);
    expect(analysis.candidates[0].principal_variation[0].move_uci).toMatch(/^[a-h][1-8]/);
    expect(analysis.candidates[0].principal_variation[1].move_uci).toMatch(/^[a-h][1-8]/);
  });

  it("resets the same game ID and checks versions", async () => {
    const bridge = new LocalChessBridge();
    const opened = await bridge.open();
    const reset = await bridge.resetGame(opened.game_id, 0, "strong");

    expect(reset.game_id).toBe(opened.game_id);
    expect(reset.difficulty).toBe("strong");
    expect(reset.version).toBe(1);
    await expect(
      bridge.playMove(reset.game_id, 0, "e2e4"),
    ).rejects.toThrow("changed");
  });
});
