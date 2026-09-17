/**
 * Getting the CSV out of the app and onto the seller's disk.
 *
 * The file itself is the server's work. What can go wrong HERE is narrower
 * and quieter, which is why it is worth tests of its own:
 *
 *  - a plain <a href="/api/listings/export.csv"> looks like it works and, in
 *    the native shell, saves a 401 page under a .csv name: that build lives
 *    on capacitor://localhost and authenticates with a bearer token, which a
 *    link does not carry. The request has to go through `api()`;
 *  - the server says what the file is called and how many listings the store
 *    holds. An absent header must not become a confident 0 — "Exported 0
 *    listings" about a file holding four hundred is worse than saying nothing;
 *  - and a seller who dismisses the iOS share sheet has not exported
 *    anything, so that cannot be reported as a success.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  EXPORT_PATH, defaultExportName, exportListingsCsv, filenameFrom, saveFile,
} from "./listingExport.js";

const { apiMock, nativeMock } = vi.hoisted(() => ({
  apiMock: vi.fn(),
  nativeMock: vi.fn(() => false),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));
vi.mock("@/lib/platform", () => ({ isNative: nativeMock }));

function response({ body = "id,title\r\n", headers = {} } = {}) {
  const lower = Object.fromEntries(
    Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  return {
    headers: { get: (name) => lower[String(name).toLowerCase()] ?? null },
    blob: async () => new Blob([body], { type: "text/csv" }),
  };
}

let clicked;

beforeEach(() => {
  clicked = [];
  apiMock.mockReset();
  nativeMock.mockReset();
  nativeMock.mockReturnValue(false);
  // jsdom implements neither, and an anchor click would otherwise try to
  // navigate. Record what the download was asked to do instead.
  URL.createObjectURL = vi.fn(() => "blob:fake");
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function () {
    clicked.push({ href: this.href, download: this.download });
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  delete navigator.share;
  delete navigator.canShare;
});

describe("the request", () => {
  it("goes through the authenticated wrapper, not a bare link", async () => {
    // The whole reason this module exists: the native build's credential is a
    // header, and a link cannot carry one.
    apiMock.mockResolvedValue(response());
    await exportListingsCsv();
    expect(apiMock).toHaveBeenCalledWith(EXPORT_PATH, { as: "response" });
  });

  it("lets the server's failure reach the caller with its own words", async () => {
    apiMock.mockRejectedValue(new Error("We couldn’t load your listings just now."));
    await expect(exportListingsCsv()).rejects.toThrow("couldn’t load your listings");
  });
});

describe("what the file is called", () => {
  it("takes the server's name", () => {
    expect(filenameFrom('attachment; filename="thryft-listings-2026-03-04.csv"'))
      .toBe("thryft-listings-2026-03-04.csv");
  });

  it("reads an unquoted name too", () => {
    expect(filenameFrom("attachment; filename=store.csv")).toBe("store.csv");
  });

  it("prefers the encoded form, which is the one that can carry an accent", () => {
    expect(filenameFrom(
      "attachment; filename=\"fallback.csv\"; filename*=UTF-8''caf%C3%A9.csv"))
      .toBe("café.csv");
  });

  it("falls back when the header is missing or unreadable", () => {
    expect(filenameFrom(null, "fallback.csv")).toBe("fallback.csv");
    expect(filenameFrom("attachment", "fallback.csv")).toBe("fallback.csv");
  });

  it("ignores a name that tries to point at a path", () => {
    // The header is the server's suggestion, and a suggestion naming a
    // directory is not one worth honouring.
    expect(filenameFrom('attachment; filename="../../etc/passwd"', "safe.csv"))
      .toBe("safe.csv");
  });

  it("dates its own fallback, so several exports do not collide", () => {
    expect(defaultExportName(new Date("2026-03-04T10:00:00Z")))
      .toBe("thryft-listings-2026-03-04.csv");
  });
});

describe("what the seller is told", () => {
  it("reports the count the server measured", async () => {
    apiMock.mockResolvedValue(response({
      headers: {
        "Content-Disposition": 'attachment; filename="store.csv"',
        "X-Export-Total": "412",
      },
    }));
    expect(await exportListingsCsv()).toMatchObject({
      how: "downloaded", filename: "store.csv", total: 412,
    });
  });

  it("does not turn a missing count into zero", async () => {
    // Number(null) is 0, and the server omits the header whenever it could
    // not count the store. A 0 here is a number nobody measured, reported as
    // one that was.
    apiMock.mockResolvedValue(response());
    expect((await exportListingsCsv()).total).toBeUndefined();
  });

  it("says how much of a store too big for one download actually came", async () => {
    apiMock.mockResolvedValue(response({
      headers: { "X-Export-Total": "40000", "X-Export-Truncated": "25000" },
    }));
    const out = await exportListingsCsv();
    expect(out.total).toBe(40000);
    expect(out.exported).toBe(25000);
  });

  it("leaves `exported` unset on an ordinary, complete export", async () => {
    apiMock.mockResolvedValue(response({ headers: { "X-Export-Total": "3" } }));
    expect((await exportListingsCsv()).exported).toBeUndefined();
  });
});

describe("saving it", () => {
  it("downloads it under the server's name on the web", async () => {
    await saveFile(new Blob(["a,b"]), "store.csv");
    expect(clicked).toEqual([{ href: "blob:fake", download: "store.csv" }]);
  });

  it("releases the blob once the browser has had it", async () => {
    vi.useFakeTimers();
    try {
      await saveFile(new Blob(["a,b"]), "store.csv");
      // Not before the click: revoking too early cancels the download in
      // Safari.
      expect(URL.revokeObjectURL).not.toHaveBeenCalled();
      vi.runAllTimers();
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:fake");
    } finally {
      vi.useRealTimers();
    }
  });

  it("uses the share sheet in the native shell, where a download does nothing",
    async () => {
      nativeMock.mockReturnValue(true);
      navigator.canShare = vi.fn(() => true);
      navigator.share = vi.fn(async () => {});
      expect(await saveFile(new Blob(["a,b"]), "store.csv")).toBe("shared");
      expect(navigator.share).toHaveBeenCalled();
      expect(clicked).toEqual([]);
    });

  it("never puts a share dialog in front of a web download", async () => {
    // navigator.share exists in desktop Chrome. On the web the browser would
    // simply save the file, and a dialog is worse than that.
    navigator.canShare = vi.fn(() => true);
    navigator.share = vi.fn(async () => {});
    expect(await saveFile(new Blob(["a,b"]), "store.csv")).toBe("downloaded");
    expect(navigator.share).not.toHaveBeenCalled();
  });

  it("falls back to a download where the platform shares text but not files",
    async () => {
      nativeMock.mockReturnValue(true);
      navigator.canShare = vi.fn(() => false);
      navigator.share = vi.fn(async () => {});
      expect(await saveFile(new Blob(["a,b"]), "store.csv")).toBe("downloaded");
      expect(navigator.share).not.toHaveBeenCalled();
      expect(clicked).toHaveLength(1);
    });

  it("treats a dismissed share sheet as a decision, not a success", async () => {
    // The caller says nothing at all for this. Reporting "exported" about a
    // file the seller declined to keep would be telling them something that
    // did not happen.
    nativeMock.mockReturnValue(true);
    navigator.canShare = vi.fn(() => true);
    navigator.share = vi.fn(async () => {
      const err = new Error("cancelled");
      err.name = "AbortError";
      throw err;
    });
    expect(await saveFile(new Blob(["a,b"]), "store.csv")).toBeNull();
    expect(clicked).toEqual([]);
  });

  it("still saves the file when the share sheet itself fails", async () => {
    nativeMock.mockReturnValue(true);
    navigator.canShare = vi.fn(() => true);
    navigator.share = vi.fn(async () => { throw new Error("no sheet here"); });
    expect(await saveFile(new Blob(["a,b"]), "store.csv")).toBe("downloaded");
    expect(clicked).toHaveLength(1);
  });
});
