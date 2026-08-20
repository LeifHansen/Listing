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
      // The React Compiler rules. These are correctness rules and the ~30
      // findings they raise are worth working through, but each one is a
      // behaviour change in code that works today, and doing them in the same
      // pass as the image workflow would bury both. Warn now, promote to
      // error as they are cleared.
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/preserve-manual-memoization": "warn",
      "react-hooks/immutability": "warn",
      "react-hooks/refs": "warn",
      "react-hooks/exhaustive-deps": "warn",
      // ...but the two that catch outright bugs stay fatal.
      "react-hooks/rules-of-hooks": "error",
    },
  },
  {
    files: ["src/**/*.test.{js,jsx}", "src/test/**/*.js", "vitest.config.js"],
    languageOptions: { globals: { ...globals.node } },
  },
];
