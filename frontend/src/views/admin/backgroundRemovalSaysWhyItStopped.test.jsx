/* Why the backgrounds stopped coming off, said to the one person who can fix it.
 *
 * A seller reads "Background removal is unavailable right now" whether the
 * Photoroom key was refused or the account ran out of images: neither is
 * theirs to fix. The operator can fix both, and used to have only a log
 * stream to tell them apart. The System tab now says which, with Photoroom's
 * own words and when.
 *
 * Rendered with react-dom + act, matching the other component tests; the repo
 * has no testing-library dependency.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminSystem } from "@/views/admin/AdminSystem";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const read = vi.hoisted(() => ({ data: null }));

vi.mock("@/views/admin/useAdminRead", () => ({
  useAdminRead: () => ({ kind: "ready", data: read.data }),
}));

const diagnostics = (photoroom) => ({
  build: "abc123", db: { configured: true, connected: true },
  storage: "r2", objstore_bucket: "b", disk_free_mb: 900,
  config_warnings: [], photoroom,
});

let root;
let host;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function render(photoroom) {
  read.data = diagnostics(photoroom);
  act(() => root.render(<AdminSystem />));
  return host.textContent;
}

describe("the System tab's background removal card", () => {
  it("names an account out of images, in Photoroom's words", () => {
    const text = render({
      configured: true, sandbox: false, failing: true,
      failures_since_success: 14,
      last_failure: {
        kind: "out_of_credits", status: 402, detail: "No credits remaining",
        reason: "The Photoroom account has no API plan, or has used all of its images.",
        at: "2026-11-22T18:04:11+00:00",
      },
      last_success_at: "2026-11-20T09:00:00+00:00",
    });
    expect(text).toContain("failing · 14 in a row");
    expect(text).toContain("(HTTP 402)");
    expect(text).toContain("has used all of its images");
    expect(text).toContain("No credits remaining");
    expect(text).toContain("Last cutout that worked");
  });

  it("keeps a fault that has cleared readable, without calling it failing", () => {
    const text = render({
      configured: true, sandbox: false, failing: false,
      failures_since_success: 0,
      last_failure: {
        kind: "key_refused", status: 401, detail: "Invalid API key",
        reason: "Photoroom refused the API key.", at: "2026-11-22T18:04:11+00:00",
      },
      last_success_at: "2026-11-22T19:00:00+00:00",
    });
    expect(text).toContain("working");
    expect(text).toContain("Last failure (HTTP 401)");
    expect(text).not.toContain("in a row");
  });

  it("says when nothing has been tried since the restart", () => {
    const text = render({
      configured: true, sandbox: false, failing: false,
      failures_since_success: 0, last_failure: null, last_success_at: null,
    });
    expect(text).toContain("no cutouts since the last restart");
    expect(text).not.toContain("Photoroom said");
  });

  it("says when there is no key at all", () => {
    const text = render({
      configured: false, sandbox: false, failing: false,
      failures_since_success: 0, last_failure: null, last_success_at: null,
    });
    expect(text).toContain("not configured");
  });

  it("renders the rest of the tab from a server that predates the card", () => {
    const text = render(undefined);
    expect(text).toContain("Integrations");
    expect(text).not.toContain("Background removal (Photoroom)");
  });
});
