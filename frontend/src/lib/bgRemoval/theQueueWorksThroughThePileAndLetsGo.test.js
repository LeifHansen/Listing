/* The device queue: one photo at a time, out of the seller's way.
 *
 * What an upload asked this device to cut is worked through serially, as
 * each photo comes back cut the screen hears about it, and the photo open in
 * the studio waits its turn at the back. A photo the device could not cut is
 * given up on WITH its reason (so it is not offered forever); a device that
 * cannot cut at all ends the run and leaves the rest pending for one that
 * can. Stop and sign-out let go of everything.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const cutPhoto = vi.fn();
vi.mock("./service", () => ({
  checkLocal: vi.fn(async () => ({ status: "ready" })),
  localUsable: () => true,
  cutPhoto: (...a) => cutPhoto(...a),
}));

import { createCutoutQueue } from "./queue";
import { LocalCutoutError } from "./errors";

let posts;
let pending;

function respond(body) {
  return Promise.resolve({ ok: true, status: 200, headers: { get: () => "application/json" },
                           json: () => Promise.resolve(body), text: () => Promise.resolve("") });
}

const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle(q) {
  for (let i = 0; i < 50 && q.getState().running; i++) await tick();
}

beforeEach(() => {
  posts = [];
  pending = [];
  cutPhoto.mockReset();
  cutPhoto.mockImplementation(async ({ name }) => ({ version: 1, updated_at: `t-${name}` }));
  vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
    const u = String(url);
    if (u.includes("/api/cutout/pending")) return respond({ photos: pending });
    posts.push({ url: u, body: opts.body ? JSON.parse(opts.body) : null });
    return respond({ ok: true });
  }));
});

afterEach(() => vi.unstubAllGlobals());

const photo = (name, sid = "s1") => ({ session_id: sid, name, kind: "item" });

describe("the device queue", () => {
  it("works through what waits, in order, telling the screen as it goes", async () => {
    const done = [];
    const finished = vi.fn();
    const q = createCutoutQueue({ onPhotoDone: (p, saved) => done.push([p.name, saved.updated_at]),
                                  onFinished: finished });
    pending = [photo("img_000.jpg"), photo("img_001.jpg")];
    await q.refresh();
    await settle(q);
    expect(cutPhoto.mock.calls.map((c) => c[0].name)).toEqual(["img_000.jpg", "img_001.jpg"]);
    expect(cutPhoto.mock.calls[0][0].origin).toBe("queue");
    expect(done).toEqual([["img_000.jpg", "t-img_000.jpg"], ["img_001.jpg", "t-img_001.jpg"]]);
    expect(finished).toHaveBeenCalledTimes(1);
    expect(q.getState()).toMatchObject({ running: false, done: 2, total: 2 });
  });

  it("asks the seller's switch, at the time, whether the cloud may help", async () => {
    let allowed = false;
    const q = createCutoutQueue({ cloudAllowed: () => allowed });
    q.add([photo("img_000.jpg")]);
    await settle(q);
    allowed = true;
    q.add([photo("img_001.jpg")]);
    await settle(q);
    expect(cutPhoto.mock.calls.map((c) => c[0].allowCloud)).toEqual([false, true]);
  });

  it("leaves the photo open in the studio until last", async () => {
    const q = createCutoutQueue();
    q.setEditing("s1", "img_000.jpg");
    q.add([photo("img_000.jpg"), photo("img_001.jpg")]);
    await tick();
    expect(cutPhoto.mock.calls[0][0].name).toBe("img_001.jpg");
    q.setEditing(null, null);
    await new Promise((r) => setTimeout(r, 2100));
    await settle(q);
    expect(cutPhoto.mock.calls.map((c) => c[0].name)).toEqual(["img_001.jpg", "img_000.jpg"]);
  }, 5000);

  it("gives up on one photo with its reason, and goes on", async () => {
    cutPhoto.mockImplementationOnce(async () => { throw new LocalCutoutError("nothing_found"); });
    const q = createCutoutQueue();
    q.add([photo("img_000.jpg"), photo("img_001.jpg")]);
    await settle(q);
    expect(posts).toHaveLength(1);
    expect(posts[0].url).toContain("/api/cutout/skip");
    expect(posts[0].body).toMatchObject({ session_id: "s1", name: "img_000.jpg" });
    expect(posts[0].body.reason).toMatch(/No item was found/);
    expect(q.getState()).toMatchObject({ done: 1, failed: 1 });
  });

  it("stops at a device that cannot cut at all, leaving the rest waiting", async () => {
    cutPhoto.mockImplementation(async () => { throw new LocalCutoutError("unsupported"); });
    const q = createCutoutQueue({ cloudAllowed: () => false });
    q.add([photo("img_000.jpg"), photo("img_001.jpg")]);
    await settle(q);
    expect(cutPhoto).toHaveBeenCalledTimes(1);
    expect(posts).toHaveLength(0);   // nothing given up on: still pending server-side
  });

  it("does not take the same photo twice", async () => {
    const q = createCutoutQueue();
    pending = [photo("img_000.jpg")];
    q.add([photo("img_000.jpg")]);
    await q.refresh();
    await settle(q);
    expect(cutPhoto).toHaveBeenCalledTimes(1);
  });

  it("lets go of everything when stopped or signed out", async () => {
    let release;
    cutPhoto.mockImplementation(() => new Promise((r) => { release = r; }));
    const done = vi.fn();
    const q = createCutoutQueue({ onPhotoDone: done });
    q.add([photo("img_000.jpg"), photo("img_001.jpg")]);
    await tick();
    q.stop();
    release({ version: 1 });
    await tick();
    expect(done).not.toHaveBeenCalled();
    expect(cutPhoto).toHaveBeenCalledTimes(1);
    expect(q.getState()).toMatchObject({ running: false, stopped: true });
    q.reset();
    expect(q.getState()).toMatchObject({ running: false, total: 0, done: 0 });
  });

  it("waits while the app is in the background", async () => {
    const q = createCutoutQueue();
    q.setPaused(true);
    q.add([photo("img_000.jpg")]);
    await tick();
    expect(cutPhoto).not.toHaveBeenCalled();
    q.setPaused(false);
    await settle(q);
    expect(cutPhoto).toHaveBeenCalledTimes(1);
  });
});
