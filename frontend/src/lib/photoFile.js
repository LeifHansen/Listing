/* A listing photo's BYTES, for code that has to read its pixels.

   /media/{session}/optimized/{name} is the photo's public address: eBay
   fetches it, <img> tags show it, and once the volume has offloaded the
   local copy it REDIRECTS to the bucket. That is right for showing a photo
   and wrong for reading one. A canvas that drew a cross-origin image is
   tainted -- every getImageData and toBlob on it throws -- so the studio's
   tools died on any photo old enough to have been offloaded, and in the
   iOS/Android shell, whose pages live on capacitor://localhost and whose
   <img> cannot carry the bearer token, on every photo.

   GET /api/image/file streams the bytes on the caller's own authenticated
   request and never redirects. `which: "source"` asks for what a background
   mask should be computed FROM (the photo as shot, while it is the faithful
   copy behind a cutout), and `sha` names the exact bytes that came back so a
   mask made from them can be refused if the photo changed meanwhile. */
import { api } from "@/lib/api";
import { mediaUrl } from "@/lib/utils";

export function photoFilePath(sessionId, name, which = "working") {
  const q = which === "source" ? "?which=source" : "";
  return `/api/image/file/${encodeURIComponent(sessionId)}/${encodeURIComponent(name)}${q}`;
}

/** {blob, sha, kind} for one photo -- `kind` is what the photo pass's screen
    said about it: "art" (a picture), "detail" (a close-up) or "". Throws
    api()'s error, with its sentence. */
export async function fetchPhoto(sessionId, name, { which = "working", signal } = {}) {
  const res = await api(photoFilePath(sessionId, name, which),
                        { as: "response", ...(signal ? { signal } : {}) });
  const blob = await res.blob();
  let sha = "";
  let kind = "";
  try {
    sha = res.headers.get("x-source-sha256") || "";
    kind = res.headers.get("x-photo-kind") || "";
  } catch (e) { /* no headers */ }
  return { blob, sha, kind };
}

export function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("Couldn't load the image."));
    img.src = src;
  });
}

/** An <img> of `blob`, decoded; the object URL is let go once it has. */
export async function imageFromBlob(blob) {
  const url = URL.createObjectURL(blob);
  try {
    return await loadImage(url);
  } finally {
    try { URL.revokeObjectURL(url); } catch (e) { /* already gone */ }
  }
}

/** The photo as a drawable <img> whose pixels can be read back, plus what
    the screen said about it ({img, kind}). Falls back to the public /media
    URL when the bytes route is unreachable from here (an older server, or
    an environment with no object URLs) -- the photo still shows, exactly as
    it always did. */
export async function loadPhoto(sessionId, name) {
  try {
    const { blob, kind } = await fetchPhoto(sessionId, name);
    return { img: await imageFromBlob(blob), kind };
  } catch (e) {
    if (e?.status === 404) throw e;
    return { img: await loadImage(`${mediaUrl(sessionId, name)}?v=${Date.now()}`), kind: "" };
  }
}
