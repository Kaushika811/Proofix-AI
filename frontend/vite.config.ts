import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import tailwindcss from "@tailwindcss/vite";
import { nitro } from "nitro/vite";
import viteReact from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  // Resolves the "@/*" alias from tsconfig.json (native in Vite 8+).
  resolve: { tsconfigPaths: true },
  server: {
    port: 5173,
    // Proxy API + WebSocket traffic to the ProoFix FastAPI backend during dev,
    // so the browser sees a single origin and CORS never applies.
    proxy: {
      "/api": {
        target: process.env.VITE_BACKEND_ORIGIN ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/ws": {
        target: process.env.VITE_BACKEND_ORIGIN ?? "http://127.0.0.1:8000",
        ws: true,
        changeOrigin: true,
      },
    },
  },
  plugins: [
    tailwindcss(),
    // Redirect TanStack Start's bundled server entry to src/server.ts
    // (our SSR error wrapper). nitro/vite builds from this.
    tanstackStart({ server: { entry: "server" } }),
    nitro(),
    viteReact(),
  ],
});
