/* Logging out has to take the credential with it, on the native build too.
 *
 * On the web the session token is a cookie and a `localStorage` entry, and
 * signing out clears both. In the Capacitor shell it lives in the **Keychain**,
 * and `storeToken(null)` asks the plugin to remove it fire-and-forget:
 *
 *     Promise.resolve(done).catch(() => {});
 *
 * The comment above that line explains the trade for a WRITE — "a Keychain
 * write that fails costs this session's persistence, not the session" — and it
 * is right. The same line also handles the REMOVE, where the trade is the
 * other way round: a removal that fails leaves a valid 30-day JWT in the
 * Keychain, `loadToken()` reads it back on the next cold start, and the app
 * opens signed in as the person who just signed out. On a shared phone that is
 * somebody else's store, and the only symptom is that logging out did not
 * work — discovered by reopening the app, not at the time.
 *
 * The fallback is the operation already known to work: overwrite the key with
 * an empty value. `loadToken` already treats a falsy value as "nothing
 * stored", so an unremovable entry becomes an unusable one.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const KEY_PATTERN = /token/i;

function nativeShell(plugin) {
  globalThis.window.Capacitor = {
    isNativePlatform: () => true,
    Plugins: { SecureStoragePlugin: plugin },
  };
}

async function freshPlatform() {
  vi.resetModules();
  vi.stubEnv("VITE_API_BASE", "https://api.example.test");
  return import("./platform");
}

afterEach(() => {
  vi.unstubAllEnvs();
  delete globalThis.window.Capacitor;
});

describe("signing out of the native shell", () => {
  let calls;
  let plugin;

  beforeEach(() => {
    calls = [];
    plugin = {
      set: vi.fn((a) => { calls.push(["set", a]); return Promise.resolve(); }),
      get: vi.fn(() => Promise.resolve({ value: "old-token" })),
      remove: vi.fn((a) => { calls.push(["remove", a]); return Promise.resolve(); }),
    };
  });

  it("removes the stored credential", async () => {
    nativeShell(plugin);
    const platform = await freshPlatform();
    await platform.storeToken(null);
    expect(plugin.remove).toHaveBeenCalled();
    expect(plugin.remove.mock.calls[0][0].key).toMatch(KEY_PATTERN);
  });

  it("blanks the entry when the Keychain refuses to remove it", async () => {
    plugin.remove = vi.fn(() => Promise.reject(new Error("keychain error -25300")));
    nativeShell(plugin);
    const platform = await freshPlatform();

    await platform.storeToken(null);

    const blanked = plugin.set.mock.calls.find(([a]) => !a.value);
    expect(blanked, "a removal that failed left the credential readable").toBeTruthy();
    expect(blanked[0].key).toMatch(KEY_PATTERN);
  });

  it("does not blank anything when the removal worked", async () => {
    nativeShell(plugin);
    const platform = await freshPlatform();
    await platform.storeToken(null);
    expect(plugin.set).not.toHaveBeenCalled();
  });

  it("still refuses to raise into the caller", async () => {
    // Logging out must not fail because the Keychain is unhappy; the
    // in-memory copy is already gone by then either way.
    plugin.remove = vi.fn(() => Promise.reject(new Error("boom")));
    plugin.set = vi.fn(() => Promise.reject(new Error("also boom")));
    nativeShell(plugin);
    const platform = await freshPlatform();

    await expect(platform.storeToken(null)).resolves.toBeUndefined();
    expect(platform.storedToken()).toBe(null);
  });

  it("still stores a token on sign-in", async () => {
    nativeShell(plugin);
    const platform = await freshPlatform();
    await platform.storeToken("fresh-token");
    expect(plugin.set).toHaveBeenCalled();
    expect(plugin.set.mock.calls[0][0].value).toBe("fresh-token");
    expect(platform.storedToken()).toBe("fresh-token");
  });

  it("leaves a blanked entry unusable on the next cold start", async () => {
    // The whole point, from the other end: what the next launch reads.
    nativeShell({
      ...plugin,
      get: vi.fn(() => Promise.resolve({ value: "" })),
    });
    const platform = await freshPlatform();
    await platform.tokenReady();
    expect(platform.storedToken()).toBe(null);
  });
});
