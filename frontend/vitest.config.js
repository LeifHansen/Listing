import { defineConfig } from "vitest/config";
import path from "node:path";

// Unit tests for the logic that has no business being verified by hand:
// the photo studio's layer/history model, and the publish gate. jsdom has no
// real 2D canvas, so the canvas tests assert the DRAWING CONTRACT (which
// layer, which composite operation, lineTo vs arc) against a recording stub
// rather than pixels — that contract is exactly what regressed before.
export default defineConfig({
  resolve: { alias: { "@": path.resolve(process.cwd(), "src") } },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{js,jsx}"],
    setupFiles: ["./src/test/setup.js"],
  },
});
