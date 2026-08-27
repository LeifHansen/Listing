import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Unit tests for the logic that has no business being verified by hand:
// the photo studio's layer/history model, and the publish gate. jsdom has no
// real 2D canvas, so the canvas tests assert the DRAWING CONTRACT (which
// layer, which composite operation, lineTo vs arc) against a recording stub
// rather than pixels — that contract is exactly what regressed before.
export default defineConfig({
  // The same JSX handling the app build uses, so a test can render a component
  // (see store.logout.test.jsx) instead of only calling into extracted logic.
  // Without it esbuild emits the classic React.createElement transform and any
  // .test.jsx file dies on "React is not defined".
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(process.cwd(), "src") } },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{js,jsx}"],
    setupFiles: ["./src/test/setup.js"],
  },
});
