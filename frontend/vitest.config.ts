/**
 * Test config, deliberately separate from `vite.config.ts`.
 *
 * Two reasons it cannot just be a `test` block on the app config:
 *
 * 1. **The TanStack Start plugin must not run.** It applies its SSR/server-entry
 *    transform across the whole module graph, and under Vitest that resolves a
 *    second copy of React — every hook then reads a null dispatcher and the
 *    component suites fail before asserting anything. Tests render components
 *    directly and need no router runtime.
 * 2. **Vitest bundles its own Vite.** Sharing one `defineConfig` makes the two
 *    Vite type trees collide on the plugin array, so `tsc` fails on a config
 *    that runs perfectly well.
 *
 * The `@` alias is restated here because Vite 8's native `tsconfigPaths`
 * resolution is not picked up by Vitest's bundled Vite.
 */
import viteReact from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    // One React instance across the graph, or hooks read a null dispatcher.
    dedupe: ["react", "react-dom"],
  },
  plugins: [viteReact()],
  test: {
    // The standalone landing preview owns its Playwright suite.
    exclude: [...configDefaults.exclude, "Landing/**"],
    // Node by default — the stream and lifecycle suites are pure logic and pay
    // nothing for a DOM. Component suites opt in per file with a
    // `// @vitest-environment jsdom` docblock.
    environment: "node",
    setupFiles: ["./src/test/setup.ts"],
  },
});
