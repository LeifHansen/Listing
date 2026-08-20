import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";

/* Lint config for the app. Scoped tightly on purpose: this is the first
   ESLint pass over a codebase that grew without one, so it enforces the rules
   that catch REAL defects (a hook called conditionally, a stale closure in a
   dependency array, a variable that does not exist) and stays quiet about
   style, which prettier-by-eye has handled fine so far. A linter that shouts
   about spacing on day one gets switched off by day two. */
export default [
  { ignores: ["dist/**", "node_modules/**", "ios/**", "android/**"] },
  js.configs.recommended,
  {
    files: ["src/**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: { ...globals.browser, ...globals.es2021 },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Empty catch blocks are a deliberate idiom here: storage access in
      // private mode, an absent Capacitor plugin, a clipboard permission.
      // Each one is commented at the site.
      "no-empty": ["error", { allowEmptyCatch: true }],
      // Unused function ARGUMENTS are usually a signature being honoured
      // (event handlers, callback shapes); unused variables are usually a bug.
      "no-unused-vars": ["error", {
        args: "none",
        caughtErrors: "none",
        varsIgnorePattern: "^_",
      }],
      // The React Compiler rules. These were staged as warnings while the
      // ~30 findings they raised were worked through ("promote to error as
      // they are cleared"). That backlog is now empty: every finding was
      // either refactored away — state derived during render instead of
      // synced in an effect, refs no longer read during render, hook returns
      // no longer mutated — or carries a targeted eslint-disable-next-line
      // with the reason it is correct at that site.
      //
      // So they are fatal now. That is the whole point of having done the
      // work: a warning nobody has to clear silently accumulates again, and
      // the next genuine defect hides in the noise. A new finding here fails
      // CI, and the fix is either the refactor or an explicit, justified
      // one-line suppression — never a quiet re-demotion of these lines.
      "react-hooks/set-state-in-effect": "error",
      "react-hooks/preserve-manual-memoization": "error",
      "react-hooks/immutability": "error",
      "react-hooks/refs": "error",
      "react-hooks/exhaustive-deps": "error",
      "react-hooks/rules-of-hooks": "error",
    },
  },
  {
    files: ["src/**/*.test.{js,jsx}", "src/test/**/*.js", "vitest.config.js"],
    languageOptions: { globals: { ...globals.node } },
  },
];
