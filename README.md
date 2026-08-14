[![CI](https://github.com/mdeclerk/StockfishGPT/actions/workflows/CI.yml/badge.svg)](https://github.com/mdeclerk/StockfishGPT/actions/workflows/CI.yml)

# StockfishGPT

[![OpenAI Apps SDK](https://img.shields.io/badge/OpenAI%20Apps%20SDK-412991?logo=openai&logoColor=white)](https://developers.openai.com/apps-sdk)
[![Stockfish](https://img.shields.io/badge/Stockfish-808080)](https://stockfishchess.org)
[![FastMCP](https://img.shields.io/badge/FastMCP-black)](https://gofastmcp.com)
[![React](https://img.shields.io/badge/React-20232A?logo=react&logoColor=61DAFB)](https://react.dev)
[![Vite](https://img.shields.io/badge/Vite-646CFF?logo=vite&logoColor=white)](https://vitejs.dev)
[![Redis](https://img.shields.io/badge/Redis-FF4438?logo=redis&logoColor=white)](https://redis.io)
[![nginx](https://img.shields.io/badge/nginx-009639?logo=nginx&logoColor=white)](https://nginx.org)
[![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)](https://www.docker.com)

StockfishGPT is an [OpenAI Apps SDK](https://developers.openai.com/apps-sdk)-based App for playing White against Stockfish in ChatGPT, with an interactive React board and engine-grounded coaching.

<p align="left">
  <img src="docs/screenshot.drawio.png" alt="StockfishGPT screenshots" width="600">
</p>

## Quick Start

1. **Install [Docker](https://docs.docker.com/get-docker/).**

2. **Start Ingress, MCP App, Redis, HTTPS Tunnel:**
   
   Spin up containers:

   ```sh
   docker compose up
   ```

   Find public tunnel url `https://*.trycloudflare.com` in console output or explicitly with

   ```sh
   docker compose logs tunnel | grep trycloudflare
   ```

3. **Add MCP App in [ChatGPT](https://www.chatgpt.com):** 

   Activate [Developer Mode](https://developers.openai.com/plugins/deploy/connect-chatgpt) (Settings → Security and login → Developer mode), then add a new plugin:
   - Name: `StockfishGPT`
   - URL: public tunnel url from step 2 — don't forget to append `/mcp`!
   - No Auth

4. **Start App:**

   ```sh
   @StockfishGPT play
   ```

## Local Development

### Prerequisites

- [Python](https://www.python.org/downloads/) & [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Node.js](https://nodejs.org/en/download) & [npm](https://docs.npmjs.com/downloading-and-installing-node-js-and-npm)
- [Stockfish](https://stockfishchess.org/download/)
- [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)

### Build & Run

Build and run with endpoint `http://localhost:8000/mcp`:

```sh
npm --prefix widget ci
npm --prefix widget run build
uv sync
uv run mcp-app
```

| Environment | CLI override | Default |
| --- | --- | --- |
| `HOST` | `--host` | `127.0.0.1` |
| `PORT` | `--port` | `8000` |
| `WIDGET_DIR` | `--widget-dir` | `widget/dist` |
| `STOCKFISH_PATH` | `--stockfish-path` | unset ─► Resolve from `PATH` |
| `REDIS_URL` | `--redis-url` | unset ─► Local in-memory storage |

### Tests

Frontend:

```sh
npm --prefix widget test
```

Backend:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

### Frontend dev server

Test React widget using Vite dev server with endpoint `http://localhost:5173/`:

```sh
npm --prefix widget run dev
```

### MCP Inspector

Run app and start the inspector `npx @modelcontextprotocol/inspector@latest` with following settings:
- URL: `http://localhost:8000/mcp`
- Transport: `Streamable HTTP`
- Connection: `Via Proxy`

### ChatGPT

Full e2e experience on ChatGPT target:

- Run app (use local in-memory game store by default, no Redis):

  ```sh
  uv run mcp-app
  ```

- Public tunnel

  ```sh
  cloudflared tunnel --no-autoupdate --url http://localhost:8000
  ```

- Add app as ChatGPT Plugin as described in [Quick Start](#quick-start)

## Architecture

### MCP Communication

The server owns each game and returns complete authoritative snapshots. The widget is a display client: it submits actions, renders the returned FEN and history, and keeps only ephemeral presentation state. MCP HTTP transport stays stateless (`stateless_http=True`). The chess service uses local in-memory store by default or Redis when `REDIS_URL` is set for production.

> ⚠️ `ui/update-model-context` cannot currently be applied reliably due to [known upstream issue #221](https://github.com/openai/openai-apps-sdk-examples/issues/221).

<p align="left">
  <img src="docs/mcp_architecture.drawio.png" alt="StockfishGPT architecture" width="600">
</p>

| MCP Tool | Arguments | Response | Visibility | R/W |
| --- | --- | --- | --- | --- |
| `start_game` | `difficulty` | `GameState` | model | W |
| `reset_game` | `game_id`, `version`, `difficulty` | `GameState` | app | W |
| `play_white_move` | `game_id`, `version`, `move_uci` | `GameState` | app | W |
| `undo_white_move` | `game_id`, `version` | `GameState` | app | W |
| `get_game_state` | `game_id` | `GameState` | model + app | R |
| `analyze_position` | `game_id` | `PositionAnalysis` | model + app | R |

### Deployment topology

```text
cloudflared ──► nginx ──► mcp-app × N ──► redis
```

### MCP-App Layers

Responsibilities are separated by layer: the MCP server exposes tools, the chess service owns game logic, the store persists game state, and the engine evaluates positions. The store runs in-process by default and switches to out-of-process Redis when `REDIS_URL` is configured.

<p align="left">
  <img src="docs/app_architecture.drawio.png" alt="StockfishGPT architecture" width="500">
</p>

## Project Structure

```text
.
├── .github/workflows/    # GitHub CI
├── src/mcp_app/
│   ├── mcp/              # FastMCP and schemas
│   ├── service/          # Chess service and models
│   ├── engine/           # Stockfish engine
│   ├── store/            # Local in-memory and Redis store
│   └── main.py           # Settings, composition root, CLI
├── widget/               # React chess widget
├── tests/                # Backend test suite
├── nginx/                # Ingress config: load balancing and rate limiting
├── Dockerfile            # Production container image
├── docker-compose.yml    # nginx, redis, mcp-app, and tunnel containers
└── pyproject.toml        # Python project config
```

## License

GPL-3.0-or-later. See [`LICENSE`](LICENSE) and [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
