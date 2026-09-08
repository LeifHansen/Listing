/* Photos picked from a phone's library have to reach the grid.
 *
 * Reported from the mobile web app: tap Browse Files, choose photos from the
 * library, the picker closes — and nothing appears. No tiles, no count, no
 * error. The photos simply went nowhere.
 *
 * The thing that makes that report hard to answer is the SILENCE. The
 * uploader dropped anything it did not recognise without a word, so a photo
 * refused here and a picker that never opened at all left exactly the same
 * screen. Whatever else was true, the seller had no way to tell which — and
 * neither did anyone reading the report. So: nothing is dropped in silence
 * any more, the file is named, and the reason is on screen.
 *
 * What it was dropping, at least in part: the client's list of image
 * extensions was NARROWER THAN THE SERVER'S. `.avif` — which current Android
 * phones produce — along with `.jfif` and `.jpe` were refused in the browser
 * for files the server decodes happily. The two lists mirror each other now
 * (services/images._EXTS), because the client refusing what the server
 * accepts is invisible from both ends.
 *
 * The editor's "Add photos" had the same shape from the other side: its DROP
 * filtered files, its PICKER did not, so the PDF one path refused the other
 * sent to the server. Both go through one rule now.
 *
 * One more, found by reading rather than by reproducing, and fixed because it
 * is wrong either way: `addFiles` read the input's LIVE FileList inside a
 * `setFiles(cur => …)` updater. The change handler clears the input the
 * moment the call returns (`e.target.value = ""`, without which picking the
 * same photo twice fires no second event) and that, per the HTML spec,
 * empties the very list the updater is holding. React's eager-state path
 * happens to evaluate the updater in line when nothing else is pending on
 * that hook — which is why the harness below could not make it fail — but
 * that is an optimisation, not a guarantee. The list is copied first now,
 * which is what the editor's own addImages has always done.
 *
 * The uploader's existing tests could not have caught any of this: they
 * install `files` as a plain value, so `value = ""` does nothing to it, and
 * they only ever hand it files that pass. The input below behaves like the
 * spec, and these hand it what a phone hands it.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { UploadPhase } from "./views/listing/UploadPhase";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;

async function mountUploader() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><UploadPhase /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

/** A file input that empties its FileList when its value is cleared.
 *
 * What every browser does, and what jsdom does not — which is the whole
 * reason this went out. */
function browserLikeInput(input, files) {
  let list = files;
  Object.defineProperty(input, "files", {
    configurable: true,
    get: () => list,
  });
  Object.defineProperty(input, "value", {
    configurable: true,
    get: () => (list.length ? `C:\\fakepath\\${list[0].name}` : ""),
    set: (v) => { if (v === "") list = []; },
  });
  return () => list;
}

let unique = 0;
function photo(name, type = "image/jpeg") {
  // Distinct bytes per photo: the pile de-duplicates on name AND size, and
  // two files identical in both really are the same photo picked twice.
  unique += 1;
  return new File([new Uint8Array(Array(unique).fill(7))], name, { type });
}

/** Pick photos the way the library picker does, through the real input. */
async function pick(files) {
  const input = host.querySelector('input[type="file"]');
  browserLikeInput(input, files);
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

const text = () => host.textContent || "";

describe("photos picked from the library", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => {
    if (root) await act(async () => { root.unmount(); });
    document.body.innerHTML = "";
    vi.unstubAllGlobals();
  });

  it("reach the grid, and the input is cleared without taking them with it",
    async () => {
      await mountUploader();
      await pick([photo("IMG_0001.jpg"), photo("IMG_0002.jpg")]);

      // The count under the pile is the seller's proof the photos arrived.
      expect(text()).toContain("2 photos");
    });

  it("arrive from an iPhone, where every one of them is called image.jpg",
    async () => {
      // Safari names library photos generically, so a whole pile of them
      // collides on name. They are still different photos and all of them
      // must land — the duplicate guard is for the same file picked twice,
      // and size is what tells the two cases apart.
      await mountUploader();
      await pick([photo("image.jpg"), photo("image.jpg")]);

      expect(text()).toContain("2 photos");
    });

  it("arrive as HEIC with no MIME type at all", async () => {
    // What an iPhone hands over when the page says it accepts .heic, and
    // what Windows/Chrome hands over for the same file: an empty type.
    await mountUploader();
    await pick([photo("IMG_0003.HEIC", "")]);

    expect(text()).toContain("1 photo");
  });

  it("says so when something it cannot use is picked", async () => {
    // The other half of "nothing happened": a file dropped for not being an
    // image left no tile AND no message, so the screen looked identical to
    // one that had lost the photos. Whatever the reason, the seller is told.
    await mountUploader();
    await pick([photo("notes.pdf", "application/pdf")]);

    expect(text()).not.toContain("1 photo");
    const toast = document.body.textContent || "";
    expect(toast.toLowerCase()).toContain("pdf");
  });

  it("keeps working for a drag-and-drop, which clears nothing", async () => {
    await mountUploader();
    const zone = host.querySelector("[class*='border-dashed']");
    await act(async () => {
      const ev = new Event("drop", { bubbles: true });
      ev.dataTransfer = { files: [photo("dragged.jpg")] };
      Object.defineProperty(ev, "dataTransfer",
        { value: { files: [photo("dragged.jpg")] } });
      zone.dispatchEvent(ev);
    });

    expect(text()).toContain("1 photo");
  });
});
