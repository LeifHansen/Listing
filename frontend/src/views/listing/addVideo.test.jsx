/* The listing's video: one file, no editing, and eBay's wait said out loud.
 *
 * eBay allows ONE video per listing and enforces it by IGNORING the extras
 * rather than refusing them — so a card that let a seller add three would
 * show three successful uploads and a listing with one video, and nothing
 * anywhere would say which. The ceiling is therefore held on this side too
 * (MAX_VIDEOS, mirroring models.MAX_VIDEOS).
 *
 * The other half is the one a seller cannot work out for themselves. A photo
 * appears on the listing the moment it publishes; a video goes into eBay's
 * moderation queue and shows up within 48 hours. Someone who publishes, looks
 * at their listing and sees no video has done nothing wrong, and this card is
 * the only place that can tell them so before they start again — which is why
 * the status the server reports is rendered rather than summarised into
 * "uploaded".
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { useListingForm } from "./useListingForm";
import { VideoCard } from "./cards";
import { MAX_VIDEOS } from "./blockers";
import { isVideoFile } from "@/lib/api";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function ok(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function fail(status, detail) {
  const body = { detail };
  return Promise.resolve({
    ok: false, status,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function Probe({ onValue }) {
  const app = useApp();
  const form = useListingForm();
  useEffect(() => { onValue({ app, form }); });
  // Mounted the way NewListing mounts it, confirm and all — the card hands
  // the removal out rather than calling it, so a test that wired it straight
  // to removeVideo would not be testing the screen.
  return <VideoCard w={form} onRemove={(name) => form.removeVideo(name, async () => true)} />;
}

let root;
let host;

/* `beforeSession` runs after the mount has settled and before the listing is
   opened. It exists for the polling tests: the status poll's interval is
   registered when the listing arrives, so fake timers have to be installed
   in that gap — before it, and the mount's own real setTimeout(0) never
   fires and the test hangs; after it, the interval is already on the real
   clock and advancing a fake one proves nothing. */
async function mountEditor(listing = {}, beforeSession) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  let value = null;
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><Probe onValue={(v) => { value = v; }} /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  beforeSession?.();
  await act(async () => {
    value.app.setSession({ sessionId: "s1", listing: { images: [], ...listing } });
  });
  return () => value;
}

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

const MP4 = () => new File(["x"], "clip.mp4", { type: "video/mp4" });

describe("adding a video", () => {
  it("uploads the file and holds what the server said about it", async () => {
    const calls = [];
    vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
      const path = String(url);
      calls.push({ path, method: opts.method || "GET", body: opts.body });
      if (path === "/api/listings/s1/video" && opts.method === "POST") {
        return ok({ ok: true, videos: [{
          file: "video_1.mp4", size: 4_000_000, on_ebay: false, status: "",
          message: "", note: "Saved. It goes to eBay when you publish.",
          url: "/media/s1/video/video_1.mp4",
        }] });
      }
      return ok({ videos: [] });
    }));
    const get = await mountEditor();

    await act(async () => { await get().form.addVideo(MP4()); });

    const post = calls.find((c) => c.method === "POST");
    expect(post.path).toBe("/api/listings/s1/video");
    expect(post.body.get("file")).toBeTruthy();
    expect(get().form.videos).toHaveLength(1);
    expect(get().form.addingVideo).toBe(false);
    // The player points at the app's own media route, so the seller can watch
    // back what they are about to publish.
    expect(host.querySelector("video")?.getAttribute("src"))
      .toBe("/media/s1/video/video_1.mp4");
  });

  it("keeps the session's copy in step so the next save can't undo it", async () => {
    /* `collect()` spreads session.listing, so a stale copy there would
       resurrect a removed video or drop a just-added one on the next
       full save. */
    vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
      if (String(url) === "/api/listings/s1/video" && opts.method === "POST") {
        return ok({ ok: true, videos: [{ file: "video_1.mp4", note: "Saved." }] });
      }
      return ok({ videos: [] });
    }));
    const get = await mountEditor();

    await act(async () => { await get().form.addVideo(MP4()); });

    expect(get().app.session.listing.videos).toEqual(
      [{ file: "video_1.mp4", note: "Saved." }]);
    expect(get().form.collect().videos).toEqual(
      [{ file: "video_1.mp4", note: "Saved." }]);
  });

  it("says why when the server refuses the file", async () => {
    vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
      if (String(url) === "/api/listings/s1/video" && opts.method === "POST") {
        return fail(400, "eBay only takes MP4 video (this one is “qt”).");
      }
      return ok({ videos: [] });
    }));
    const get = await mountEditor();

    await act(async () => { await get().form.addVideo(MP4()); });

    expect(get().form.videos).toEqual([]);
    expect(document.body.textContent).toContain("eBay only takes MP4 video");
  });

  it("removes the video from the listing and from the card", async () => {
    const calls = [];
    vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
      calls.push({ path: String(url), method: opts.method || "GET" });
      return ok({ ok: true, videos: [] });
    }));
    const get = await mountEditor({
      videos: [{ file: "video_1.mp4", note: "On eBay.",
                 url: "/media/s1/video/video_1.mp4" }],
    });
    expect(get().form.videos).toHaveLength(1);

    await act(async () => {
      await get().form.removeVideo("video_1.mp4", async () => true);
    });

    expect(calls.some((c) => c.method === "DELETE"
      && c.path === "/api/listings/s1/video/video_1.mp4")).toBe(true);
    expect(get().form.videos).toEqual([]);
    expect(get().app.session.listing.videos).toEqual([]);
    expect(host.querySelector("video")).toBe(null);
  });

  it("asks before it removes — the video is gone with no undo", async () => {
    /* Re-shooting a clip, or re-sending 150MB over a phone connection, is a
       long way back from a mis-tap. The photo delete is confirmed for the
       same reason. */
    const calls = [];
    vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
      calls.push({ path: String(url), method: opts.method || "GET" });
      return ok({ ok: true, videos: [] });
    }));
    const get = await mountEditor({
      videos: [{ file: "video_1.mp4", note: "On eBay.",
                 url: "/media/s1/video/video_1.mp4" }],
    });

    await act(async () => {
      await get().form.removeVideo("video_1.mp4", async () => false);
    });

    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    expect(get().form.videos).toHaveLength(1);
  });
});


describe("eBay's one-video limit", () => {
  it("is the same number the backend holds", () => {
    expect(MAX_VIDEOS).toBe(1);
  });

  it("refuses a second video on the card rather than letting eBay drop it", async () => {
    const calls = [];
    vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
      calls.push({ path: String(url), method: opts.method || "GET" });
      return ok({ videos: [] });
    }));
    await mountEditor({
      videos: [{ file: "video_1.mp4", note: "On eBay.",
                 url: "/media/s1/video/video_1.mp4" }],
    });

    // With a video already there the card offers no picker at all — there is
    // nothing to click, which is the clearest way to say "one".
    expect(host.querySelector("input[type=file]")).toBe(null);
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("only offers a picker that asks for MP4", async () => {
    vi.stubGlobal("fetch", vi.fn(() => ok({ videos: [] })));
    await mountEditor();
    const input = host.querySelector("input[type=file]");
    expect(input.getAttribute("accept")).toContain("video/mp4");
    // Never `multiple`: eBay takes one, and a picker that let a seller select
    // four would be a promise the listing cannot keep.
    expect(input.hasAttribute("multiple")).toBe(false);
  });

  it("knows an mp4 from everything else a picker can return", () => {
    expect(isVideoFile(new File([""], "a.mp4", { type: "video/mp4" }))).toBe(true);
    // Phones and some desktops hand over an empty MIME type; the name is the
    // only thing left to go on.
    expect(isVideoFile(new File([""], "a.mp4", { type: "" }))).toBe(true);
    expect(isVideoFile(new File([""], "a.mov", { type: "video/quicktime" }))).toBe(false);
    expect(isVideoFile(new File([""], "a.jpg", { type: "image/jpeg" }))).toBe(false);
  });
});


describe("what eBay is doing with it", () => {
  it("shows eBay's own wait rather than calling the upload done", async () => {
    /* The 48 hours is the whole reason this is worth rendering: a seller who
       publishes and sees no video on eBay has done nothing wrong. */
    vi.stubGlobal("fetch", vi.fn(() => ok({ videos: [] })));
    await mountEditor({
      videos: [{
        file: "video_1.mp4", on_ebay: true, status: "PROCESSING", message: "",
        url: "/media/s1/video/video_1.mp4",
        note: "eBay is reviewing this video. It appears on the listing once "
          + "that's done — usually within 48 hours.",
      }],
    });

    expect(host.textContent).toContain("within 48 hours");
  });

  it("passes on eBay's reason when eBay refused the video", async () => {
    /* The refusal lands days after the seller stopped looking, and eBay tells
       nobody else. Swallowing it leaves a listing that will never have a
       video and a seller with no idea why. */
    vi.stubGlobal("fetch", vi.fn(() => ok({ videos: [] })));
    await mountEditor({
      videos: [{
        file: "video_1.mp4", on_ebay: true, status: "BLOCKED",
        message: "Video contains prohibited content.",
        url: "/media/s1/video/video_1.mp4",
        note: "eBay wouldn't accept this video. Remove it and add a different one.",
      }],
    });

    expect(host.textContent).toContain("Video contains prohibited content.");
    expect(host.textContent).toContain("Remove it and add a different one");
  });

  it("asks again while eBay is still looking at it", async () => {
    let polls = 0;
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url) === "/api/listings/s1/video") polls += 1;
      return ok({ videos: [{ file: "video_1.mp4", status: "PROCESSING",
                             note: "eBay is reviewing this video." }] });
    }));
    try {
      await mountEditor({
        videos: [{ file: "video_1.mp4", status: "PROCESSING",
                   note: "eBay is reviewing this video." }],
      }, () => vi.useFakeTimers());

      await act(async () => { await vi.advanceTimersByTimeAsync(60000); });

      expect(polls).toBeGreaterThan(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops asking eBay once it has finished with the video", async () => {
    /* A terminal status is settled: LIVE or BLOCKED will not change again.
       Polling on regardless would turn every open tab into a request every
       twenty seconds for the life of the listing. */
    let polls = 0;
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url) === "/api/listings/s1/video") polls += 1;
      return ok({ videos: [{ file: "video_1.mp4", status: "LIVE",
                             note: "On eBay." }] });
    }));
    try {
      await mountEditor({
        videos: [{ file: "video_1.mp4", status: "LIVE", note: "On eBay." }],
      }, () => vi.useFakeTimers());

      await act(async () => { await vi.advanceTimersByTimeAsync(120000); });

      expect(polls).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });
});


describe("a video that lives on eBay", () => {
  it("says so rather than rendering a player with nothing behind it", async () => {
    /* An imported listing's video is eBay's — this app never held the bytes
       and never will, so there is nothing to play back here. */
    vi.stubGlobal("fetch", vi.fn(() => ok({ videos: [] })));
    await mountEditor({
      videos: [{ file: "", on_ebay: true, status: "LIVE", url: "",
                 note: "On eBay." }],
    });

    expect(host.querySelector("video")).toBe(null);
    expect(host.textContent).toContain("hosted by eBay");
  });
});
