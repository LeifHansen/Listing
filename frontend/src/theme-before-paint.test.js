/* The pre-paint theme script has to read the same keys the app writes.
 *
 * The inline <script> in index.html is the ONLY thing that reads the stored
 * theme. It runs before the bundle exists, sets `dark` on <html>, and the
 * store then seeds its React state from that class rather than from storage
 * -- so a preference the inline script misses is a preference nothing else
 * looks for. It also cannot import anything, since nothing has loaded yet,
 * which is why it hand-rolls a read that the rest of the app gets from
 * lib/localPrefs. Two copies of one contract, in two languages of the same
 * codebase: that is how they drift.
 *
 * They drifted here. Renaming the storage keys from `quickflip-*` to
 * `thryft-*` updated every module that goes through localPrefs and left this
 * script reading the old name -- and since the toggle now WRITES the new
 * name, the script read a key nothing wrote. Dark mode stopped working
 * outright: not a flash before it settles, but every seller in light mode on
 * every load, with the toggle agreeing, and nothing failing anywhere.
 *
 * So this asserts the coupling directly, against the real files.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "..", "index.html"), "utf8");
const prefs = readFileSync(join(here, "lib", "localPrefs.js"), "utf8");

// The inline script only -- not the module tag, and not the boot splash.
const match = html.match(/<script>([\s\S]*?)<\/script>/);
const inline = match ? match[1] : "";

// The prefixes localPrefs actually uses, read out of its source rather than
// hardcoded, so renaming there fails this test instead of quietly passing it.
function prefixOf(constant) {
  const m = prefs.match(new RegExp(`const ${constant} = \\(name\\) => \`([a-z-]+)\\$`));
  if (!m) throw new Error(`localPrefs no longer defines ${constant} as a prefix`);
  return m[1];
}

describe("the theme applied before first paint", () => {
  it("reads the key the app writes today", () => {
    expect(inline).toContain(`${prefixOf("NEW")}theme`);
  });

  it("still reads the pre-rename key, for sellers who have not migrated", () => {
    // Their stored theme lives under the old name until their first load
    // finishes. Dropping this fallback flashes white at exactly the sellers
    // the migration is for.
    expect(inline).toContain(`${prefixOf("OLD")}theme`);
  });

  it("prefers the current key over the legacy one", () => {
    const current = inline.indexOf(`${prefixOf("NEW")}theme`);
    const legacy = inline.indexOf(`${prefixOf("OLD")}theme`);
    expect(current).toBeGreaterThanOrEqual(0);
    expect(legacy).toBeGreaterThan(current);
  });

  it("cannot throw the page away when storage is blocked", () => {
    // Safari with site data blocked throws on getItem. Unguarded, that kills
    // the whole inline script -- and it is in <head>, before the app.
    expect(inline).toMatch(/try\s*\{/);
    expect(inline).toMatch(/catch/);
  });
});
