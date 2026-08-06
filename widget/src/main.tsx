import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { LocalChessBridge } from "./chess/localBridge";
import { McpChessBridge } from "./chess/mcpBridge";
import "./styles.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("Missing root element");
}

// Embedded in ChatGPT the board talks to the server and the host; standalone
// it runs against the local stand-in, with no host integration at all.
const embedded = window.parent !== window;
const bridge = embedded ? new McpChessBridge() : new LocalChessBridge();

createRoot(root).render(
  <StrictMode>
    <App
      bridge={bridge}
      host={bridge instanceof McpChessBridge ? bridge : undefined}
    />
  </StrictMode>,
);
