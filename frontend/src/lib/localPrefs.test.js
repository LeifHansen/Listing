import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { readLocal, writeLocal, clearLocal } from "./localPrefs";

describe("localPrefs", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it("reads what it wrote", () => {
    writeLocal("theme", "dark");
    expect(readLocal("theme")).toBe("dark");
    expect(localStorage.getItem("thryft-theme")).toBe("dark");
  });

  it("still finds a value stored under the old name", () => {
    localStorage.setItem("quickflip-theme", "dark");
    expect(readLocal("theme")).toBe("dark");
  });

  it("carries the old value forward and drops the old key", () => {
    localStorage.setItem("quickflip-bulk", '{"jobId":"job-1"}');
    expect(readLocal("bulk")).toBe('{"jobId":"job-1"}');
    expect(localStorage.getItem("thryft-bulk")).toBe('{"jobId":"job-1"}');
    expect(localStorage.getItem("quickflip-bulk")).toBe(null);
  });

  it("prefers the current key when both exist", () => {
    localStorage.setItem("quickflip-theme", "light");
    localStorage.setItem("thryft-theme", "dark");
    expect(readLocal("theme")).toBe("dark");
  });

  it("a write removes the stale legacy copy so the migration finishes", () => {
    localStorage.setItem("quickflip-theme", "light");
    writeLocal("theme", "dark");
    expect(localStorage.getItem("quickflip-theme")).toBe(null);
  });

  it("clears both names", () => {
    localStorage.setItem("quickflip-bulk", "a");
    localStorage.setItem("thryft-bulk", "b");
    clearLocal("bulk");
    expect(readLocal("bulk")).toBe(null);
  });

  it("answers null when storage throws, instead of throwing", () => {
    /* Safari with site data blocked, and an iOS WKWebView without storage —
       this app ships to iOS. */
    vi.stubGlobal("localStorage", {
      getItem() { throw new DOMException("denied"); },
      setItem() { throw new DOMException("denied"); },
      removeItem() { throw new DOMException("denied"); },
    });
    expect(readLocal("theme")).toBe(null);
    expect(() => writeLocal("theme", "dark")).not.toThrow();
    expect(() => clearLocal("theme")).not.toThrow();
  });

  it("still answers from the old key when the forward write is refused", () => {
    const store = { "quickflip-theme": "dark" };
    vi.stubGlobal("localStorage", {
      getItem: (k) => (k in store ? store[k] : null),
      setItem() { throw new DOMException("read-only"); },
      removeItem() { throw new DOMException("read-only"); },
    });
    expect(readLocal("theme")).toBe("dark");
  });
});
