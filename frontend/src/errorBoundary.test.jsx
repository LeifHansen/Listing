/* A crash shows something a seller can act on, and tells the server.
 *
 * Before this, a throw during render unmounted the whole tree and left a white
 * screen: nothing on the page, nothing in a log, nothing on the server. The
 * only evidence a crash had happened was a seller saying the app "went blank".
 *
 * Two properties are easy to lose here and both are asserted.
 *
 * StrictMode renders twice on purpose in development. A boundary that reported
 * per render would double every count in the table, so the reporter dedupes by
 * fingerprint and this pins that it works through a real double render.
 *
 * The boundary still calls console.error. scripts/smoke.mjs fails the CI smoke
 * gate on a page error, and it is the only gate that catches "a screen a
 * seller cannot open". Swallowing the crash would make the app look healthier
 * to CI while being exactly as broken — the worst of the available outcomes.
 */
import { StrictMode } from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { _resetForTests } from "@/lib/clientErrors";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host, root, posts, consoleErrors;

function Boom() { throw new TypeError("render exploded"); }

beforeEach(() => {
  _resetForTests();
  posts = [];
  consoleErrors = [];
  vi.stubGlobal("fetch", vi.fn((url, opts) => {
    posts.push(JSON.parse(opts.body));
    return Promise.resolve({ ok: true, status: 202 });
  }));
  // React itself logs caught errors; keep the noise out of the run while
  // still counting what the boundary contributes.
  vi.spyOn(console, "error").mockImplementation((...a) => consoleErrors.push(a));
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("when a child throws", () => {
  it("renders a recoverable panel instead of a blank page", () => {
    act(() => root.render(<ErrorBoundary><Boom /></ErrorBoundary>));

    expect(host.textContent).toContain("This screen ran into a problem");
    expect(host.querySelector("button").textContent).toBe("Reload");
    expect(host.querySelector("[role=alert]")).toBeTruthy();
  });

  it("reports the crash with the component stack", () => {
    act(() => root.render(<ErrorBoundary><Boom /></ErrorBoundary>));

    expect(posts).toHaveLength(1);
    expect(posts[0].kind).toBe("react");
    expect(posts[0].message).toBe("render exploded");
    expect(posts[0].component_stack).toContain("Boom");
  });

  it("still logs to the console, so the CI smoke gate can see it", () => {
    act(() => root.render(<ErrorBoundary><Boom /></ErrorBoundary>));

    expect(consoleErrors.length).toBeGreaterThan(0);
  });

  it("reports once even though StrictMode renders twice", () => {
    act(() => root.render(
      <StrictMode><ErrorBoundary><Boom /></ErrorBoundary></StrictMode>,
    ));

    expect(posts).toHaveLength(1);
  });

  it("uses a caller's fallback when one is given", () => {
    act(() => root.render(
      <ErrorBoundary fallback={<p>the shell is still here</p>}>
        <Boom />
      </ErrorBoundary>,
    ));

    expect(host.textContent).toBe("the shell is still here");
  });
});

describe("when nothing throws", () => {
  it("renders its children and reports nothing", () => {
    act(() => root.render(<ErrorBoundary><p>all fine</p></ErrorBoundary>));

    expect(host.textContent).toBe("all fine");
    expect(posts).toHaveLength(0);
  });
});
