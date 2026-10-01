import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// Dev server proxies API + media calls to the FastAPI backend so `npm run dev`
// works against a locally running `./run.sh` backend.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/media": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // Split the two big, rarely-changing dependencies out of the app
        // chunk. They are their own cache entries, so shipping an app change
        // (the common case) no longer invalidates ~250KB of vendor code the
        // browser already had — worth real seconds on the phone connections
        // this app is used on. Everything is still loaded eagerly; this is a
        // caching split, not lazy loading, so nothing about startup order
        // or the boot splash changes.
        //
        // The function form: Vite 8 (rolldown) no longer accepts the object
        // shorthand and fails the build with "manualChunks is not a function".
        manualChunks(id) {
          if (/node_modules\/(react|react-dom|scheduler)\//.test(id)) return "react";
          if (/node_modules\/(framer-motion|motion-dom|motion-utils)\//.test(id)) return "motion";
          return undefined;
        },
      },
    },
  },
});
