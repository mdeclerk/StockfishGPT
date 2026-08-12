# Third-party notices

This project is licensed under GPL-3.0-or-later and depends on third-party software with its own copyright and license terms.

Runtime dependencies include:

- `chess` / python-chess — GPL-3.0-or-later.
- MCP Python SDK — MIT.
- redis-py — MIT.
- React and React DOM — MIT.
- MCP Apps SDK — MIT.
- chess.js — BSD-2-Clause.
- react-chessboard — MIT.
- Vite, Vitest, TypeScript, and test utilities — their respective open-source
  licenses.

The container includes the official precompiled Stockfish 18 binary under GPL-3.0-or-later. The build verifies the release asset against its pinned SHA-256 digest. The exact release tag, upstream license, and corresponding source link are available in the container at `/usr/share/doc/stockfish/`; releases and their source are also available from <https://github.com/official-stockfish/Stockfish/releases>.
