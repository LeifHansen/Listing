/* A screen's chunk missing after a deploy reloads the page once, and only once. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { installStaleBuildReload } from "./staleBuild";

function fakeWindow() {
  const store = new Map();
  const target = new EventTarget();
  return Object.assign(target, {
    sessionStorage: {
      getItem: (k) => store.get(k) ?? null,
      setItem: (k, v) => store.set(k, v),
    },
    location: { reload: vi.fn() },
  });
}

function chunkMissing(win) {
  const e = new Event("vite:preloadError", { cancelable: true });
  win.dispatchEvent(e);
  return e.defaultPrevented;
}

afterEach(() => vi.useRealTimers());

describe("a lazily loaded screen whose file is gone", () => {
  it("reloads onto the new build, and doesn't let the error through", () => {
    const win = fakeWindow();
    installStaleBuildReload(win);
    expect(chunkMissing(win)).toBe(true);
    expect(win.location.reload).toHaveBeenCalledTimes(1);
  });

  it("lets a second failure in the same minute reach the error screen", () => {
    vi.useFakeTimers();
    const win = fakeWindow();
    installStaleBuildReload(win);
    chunkMissing(win);
    expect(chunkMissing(win)).toBe(false);
    expect(win.location.reload).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(61_000);
    expect(chunkMissing(win)).toBe(true);
  });

  it("does nothing clever without session storage", () => {
    const win = fakeWindow();
    win.sessionStorage.getItem = () => { throw new Error("blocked"); };
    installStaleBuildReload(win);
    expect(chunkMissing(win)).toBe(false);
    expect(win.location.reload).not.toHaveBeenCalled();
  });
});
