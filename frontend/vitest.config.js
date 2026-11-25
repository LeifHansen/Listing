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
    // Each test file in its own VM context inside a long-lived worker. The
    // default pool started a fresh process and built a whole jsdom for every
    // one of the 150-odd files, which was half of the run's time; this keeps
    // the files isolated from each other (module mocks and stubbed globals do
    // not leak) at about a third of the CPU. The one thing it does not allow
    // is replacing `window` itself with vi.stubGlobal -- spy on the method
    // instead (see lib/aiConsent.test.js).
    pool: "vmThreads",
    include: ["src/**/*.test.{js,jsx}"],
    setupFiles: ["./src/test/setup.js"],
  },
});
