import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig({
  base: "./",
  plugins: [react(), inlineAppBundle()],
  build: {
    assetsInlineLimit: Number.MAX_SAFE_INTEGER,
    cssCodeSplit: false,
    modulePreload: false,
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});

/** Inline Vite assets. */
function inlineAppBundle(): Plugin {
  return {
    name: "inline-mcp-app",
    enforce: "post",
    generateBundle(_, bundle) {
      const htmlAsset = bundle["index.html"];
      if (!htmlAsset || htmlAsset.type !== "asset") {
        throw new Error("Vite did not emit index.html");
      }
      let html = String(htmlAsset.source);

      for (const [fileName, output] of Object.entries(bundle)) {
        if (output === htmlAsset) continue;
        const escapedName = escapeRegExp(fileName);
        if (output.type === "chunk") {
          const script = new RegExp(
            `<script[^>]+src=["'](?:\\./|/)${escapedName}["'][^>]*></script>`,
          );
          html = html.replace(
            script,
            () =>
              `<script type="module">${escapeClosingTag(output.code, "script")}</script>`,
          );
        } else if (fileName.endsWith(".css")) {
          const stylesheet = new RegExp(
            `<link[^>]+href=["'](?:\\./|/)${escapedName}["'][^>]*>`,
          );
          html = html.replace(
            stylesheet,
            () =>
              `<style>${escapeClosingTag(String(output.source), "style")}</style>`,
          );
        }
        delete bundle[fileName];
      }

      if (/<script[^>]+src=|<link[^>]+rel=["']stylesheet/i.test(html)) {
        throw new Error("MCP App build still contains external bundle references");
      }
      htmlAsset.source = html;
    },
  };
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function escapeClosingTag(value: string, tag: string): string {
  return value.replace(new RegExp(`</${tag}`, "gi"), `<\\/${tag}`);
}
