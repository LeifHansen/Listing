// eBay Listing Generator - frontend logic
const $ = (id) => document.getElementById(id);

const CONDITIONS = [
  "NEW", "NEW_OTHER", "NEW_WITH_DEFECTS", "CERTIFIED_REFURBISHED",
  "SELLER_REFURBISHED", "USED_EXCELLENT", "USED_VERY_GOOD", "USED_GOOD",
  "USED_ACCEPTABLE", "FOR_PARTS_OR_NOT_WORKING",
];

const state = {
  sessionId: null,
  files: [],
  listing: null,
  ebayConfigured: false,   // server-level env credentials (legacy path)
  ebayConnected: false,    // the logged-in user's own eBay connection
  ebayEnv: "",
  ebayUsername: "",        // which eBay account is linked
  ebayEmail: "",
  ebayPoliciesData: null,  // cached business-policy lists + selection
  taxonomyConfigured: false,
  user: null,
  authMode: "login",
  mode: "sell",            // "shop" | "sell"
  shopSession: null,       // session id of the item currently being scanned
  shopListing: null,       // the scanned item's identified listing
  bulkItems: null,         // bulk-mode queue items
};

// Set when "Buy" is tapped while logged out, so the action resumes after login.
let pendingBuy = false;

// Publishing is live if EITHER the user connected their eBay account or the
// server has env-level credentials.
function canPublishLive() {
  return state.ebayConnected || state.ebayConfigured;
}

// ---------- helpers ----------
function showSpinner(text) {
  $("spinner-text").textContent = text || "Working…";
  $("spinner").classList.remove("hidden");
}
function hideSpinner() { $("spinner").classList.add("hidden"); }

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new Error("Network error — the server may be starting up. Try again in a few seconds.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(`(${res.status}) ${detail}`);
  }
  return res.json();
}

// ---------- health ----------
async function loadHealth() {
  try {
    const h = await api("/api/health");
    state.ebayConfigured = h.ebay_configured;
    state.taxonomyConfigured = h.taxonomy_configured;
    const bar = $("status-bar");
    bar.innerHTML = "";
    const pill = (label, ok, title) => {
      const s = document.createElement("span");
      s.className = "pill " + (ok ? "ok" : "warn");
      s.textContent = label;
      if (title) s.title = title;
      return s;
    };
    bar.appendChild(pill(
      h.anthropic_configured ? "AI: ready" : "AI: missing ANTHROPIC_API_KEY",
      h.anthropic_configured));
    const ebayPill = pill("", false);
    ebayPill.id = "pill-ebay";
    bar.appendChild(ebayPill);
    renderEbayPill();
    bar.appendChild(pill(
      h.taxonomy_configured ? "Categories: auto" : "Categories: manual",
      h.taxonomy_configured));
  } catch (e) { /* ignore */ }
}

// ---------- step 1: upload ----------
let _thumbUrls = [];
function renderThumbs() {
  const box = $("thumbs");
  // Revoke previously-created object URLs so re-rendering (add more, start new)
  // doesn't leak blobs — matters on memory-limited phones.
  _thumbUrls.forEach((u) => URL.revokeObjectURL(u));
  _thumbUrls = [];
  box.innerHTML = "";
  state.files.forEach((f) => {
    const url = URL.createObjectURL(f);
    _thumbUrls.push(url);
    const img = document.createElement("img");
    img.src = url;
    box.appendChild(img);
  });
  $("btn-process").disabled = state.files.length === 0;
}

// HEIC/HEIF often arrive with an empty MIME type (Chrome, Windows), so accept
// by extension too — the server decodes anything Pillow can.
const IMAGE_EXT_RE = /\.(jpe?g|png|webp|bmp|gif|tiff?|heic|heif|hif)$/i;
function addFiles(fileList) {
  for (const f of fileList) {
    if (!f.type.startsWith("image/") && !IMAGE_EXT_RE.test(f.name || "")) continue;
    // Skip duplicates (same file picked twice) so we don't upload it twice.
    if (state.files.some((e) => e.name === f.name && e.size === f.size)) continue;
    state.files.push(f);
  }
  renderThumbs();
}

function setupDropzone() {
  const dz = $("dropzone");
  const input = $("file-input");
  dz.addEventListener("click", () => input.click());
  input.addEventListener("change", () => addFiles(input.files));
  ["dragover", "dragenter"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
}

// Phone photos are often 5-12MB; the server only needs ~1600px. Re-encoding
// in the browser before upload cuts transfer time ~10x. Formats the browser
// can't decode (e.g. HEIC) fall through and upload as-is — the server
// handles them.
const MAX_UPLOAD_SIDE = 2000;
async function downscaleForUpload(file) {
  try {
    let bmp;
    try {
      bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch (e) {
      bmp = await createImageBitmap(file);
    }
    const scale = Math.min(1, MAX_UPLOAD_SIDE / Math.max(bmp.width, bmp.height));
    if (scale >= 1 && file.size < 2 * 1024 * 1024) { bmp.close(); return file; }
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bmp.width * scale));
    canvas.height = Math.max(1, Math.round(bmp.height * scale));
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    bmp.close();
    const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.9));
    if (!blob) return file;
    const name = (file.name || "photo").replace(/\.\w+$/, "") + ".jpg";
    return new File([blob], name, { type: "image/jpeg" });
  } catch (e) {
    return file; // never block the upload on a client-side optimization
  }
}

// Guard against double-submits: the spinner is a non-blocking corner toast, so
// without this a double-click or Enter-repeat fires the request twice (two AI
// charges, two drafts, or racing publishes).
const _inFlight = {};
function once(key, fn) {
  return async (...args) => {
    if (_inFlight[key]) return;
    _inFlight[key] = true;
    try { return await fn(...args); }
    finally { _inFlight[key] = false; }
  };
}

async function processImages() {
  if ($("opt-bulk").checked) return bulkStart();
  try {
    const removeBg = $("opt-remove-bg").checked;
    showSpinner("Preparing photos…");
    const prepped = await Promise.all(state.files.map(downscaleForUpload));
    showSpinner(removeBg ? "Uploading & removing backgrounds…" : "Uploading & optimizing images…");
    const fd = new FormData();
    prepped.forEach((f) => fd.append("files", f));
    fd.append("remove_bg", removeBg ? "true" : "false");
    const up = await api("/api/upload", { method: "POST", body: fd });
    state.sessionId = up.session_id;

    showSpinner("Identifying with AI lens…");
    const result = await api(`/api/identify/${state.sessionId}`, { method: "POST" });
    state.listing = result.listing;
    renderPreview(result);
    showView("preview");
  } catch (e) {
    alert("Error: " + e.message);
  } finally {
    hideSpinner();
  }
}

// ---------- mode toggle (Shop vs Sell) ----------
const SELL_VIEWS = ["step-upload", "step-preview", "step-listings"];
const SELL_NAV = ["nav-new", "nav-listings", "nav-images", "nav-edit"];

function setMode(mode) {
  state.mode = mode;
  const shop = mode === "shop";
  $("mode-shop").classList.toggle("active", shop);
  $("mode-sell").classList.toggle("active", !shop);
  $("step-shop").classList.toggle("hidden", !shop);
  // Sell-only nav buttons don't apply in Shop mode.
  SELL_NAV.forEach((id) => $(id).classList.toggle("hidden", shop));
  if (shop) {
    SELL_VIEWS.forEach((id) => $(id).classList.add("hidden"));
  } else {
    // Back to Sell: resume wherever the seller was (preview if a draft is open).
    showView(state.listing ? "preview" : "upload");
  }
}

// ---------- Shop Mode ----------
async function shopScan(file) {
  if (!file) return;
  try {
    showSpinner("Scanning item…");
    const prepped = await downscaleForUpload(file);
    const fd = new FormData();
    fd.append("files", prepped);
    fd.append("remove_bg", "false");
    const up = await api("/api/upload", { method: "POST", body: fd });
    state.shopSession = up.session_id;

    showSpinner("Identifying with AI lens…");
    const res = await api(`/api/identify/${up.session_id}`, { method: "POST" });
    state.shopListing = res.listing;

    // Best-effort market price (never block the scan on it).
    let price = null;
    if (state.taxonomyConfigured) {
      showSpinner("Checking eBay prices…");
      try {
        const l = res.listing;
        const query = [l.brand, l.title].filter(Boolean).join(" ").trim();
        const pd = await api("/api/price-suggestions", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query, category_id: l.category_id || null, condition: l.condition || null }),
        });
        price = pd.suggestion;
      } catch (e) { /* price is optional */ }
    }
    renderShopResult(res, price);
  } catch (e) {
    alert("Scan error: " + e.message);
  } finally {
    hideSpinner();
  }
}
const shopScanOnce = once("shop", shopScan);

function renderShopResult(res, price) {
  const l = res.listing || {};
  const box = $("shop-result");
  const img = (l.images && l.images[0])
    ? `/media/${state.shopSession}/optimized/${l.images[0]}` : "";
  const conf = ["low", "medium", "high"].includes(res.confidence) ? res.confidence : "medium";
  const priceHtml = price
    ? `<div class="shop-price">≈ $${price.price}</div>
       <div class="hint">typical $${price.low}–$${price.high} · ${price.count} comps · ${escapeHtml(price.basis)}${price.sold_data ? "" : " (asking, not sold)"}</div>`
    : `<div class="hint">No price estimate yet — you can set one later.</div>`;
  box.innerHTML =
    (img ? `<img class="shop-photo" src="${img}" alt="scanned item" />` : "") +
    `<div class="shop-info">
       <div class="shop-title">${escapeHtml(l.title || "(couldn't identify — try another angle)")}</div>
       <div class="hint">${escapeHtml(l.condition || "")} · AI confidence:
         <span class="badge ${conf}">${conf.toUpperCase()}</span></div>
       ${priceHtml}
     </div>
     <div class="shop-actions">
       <button id="shop-buy" class="primary" type="button">＋ Buy — add to inventory</button>
       <button id="shop-again" class="ghost" type="button">↻ Scan another</button>
     </div>`;
  box.classList.remove("hidden");
  $("shop-buy").addEventListener("click", buyItemOnce);
  $("shop-again").addEventListener("click", resetShop);
}

function resetShop() {
  state.shopSession = null;
  state.shopListing = null;
  $("shop-input").value = "";
  $("shop-result").classList.add("hidden");
  $("shop-result").innerHTML = "";
}

async function buyItem() {
  if (!state.shopSession || !state.shopListing) return;
  if (!state.user) { pendingBuy = true; openAuthModal(); return; }
  try {
    showSpinner("Adding to your inventory…");
    await api("/api/inventory/add", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.shopSession, listing: state.shopListing }),
    });
    resetShop();
    alert("Added to your inventory! Switch to Sell → My listings to finish and publish it.");
  } catch (e) {
    alert("Couldn't add to inventory: " + e.message);
  } finally {
    hideSpinner();
  }
}
const buyItemOnce = once("buy", buyItem);

// ---------- Shop Mode: shelf scan (video → frames → triage) ----------
// Sample up to `maxFrames` evenly-spaced JPEG frames from a recorded video,
// scaled down so the upload stays small. Runs entirely in the browser.
async function extractFrames(file, maxFrames = 6) {
  const url = URL.createObjectURL(file);
  const video = document.createElement("video");
  video.muted = true; video.playsInline = true; video.preload = "auto"; video.src = url;
  try {
    await new Promise((res, rej) => {
      video.onloadedmetadata = () => res();
      video.onerror = () => rej(new Error("Couldn't read that video."));
    });
    const dur = (isFinite(video.duration) && video.duration > 0) ? video.duration : 0;
    const vw = video.videoWidth || 640, vh = video.videoHeight || 480;
    const scale = Math.min(1, 1024 / Math.max(vw, vh));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(vw * scale));
    canvas.height = Math.max(1, Math.round(vh * scale));
    const ctx = canvas.getContext("2d");
    const grab = (t) => new Promise((res) => {
      let done = false;
      const finish = (b) => { if (!done) { done = true; video.removeEventListener("seeked", onSeeked); res(b); } };
      const onSeeked = () => {
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        canvas.toBlob((b) => finish(b), "image/jpeg", 0.85);
      };
      video.addEventListener("seeked", onSeeked);
      setTimeout(() => finish(null), 4000);  // don't hang if 'seeked' never fires
      video.currentTime = t;
    });
    const frames = [];
    if (dur) {
      const n = Math.max(1, maxFrames);
      for (let i = 0; i < n; i++) {
        const t = Math.min(dur - 0.05, (dur * (i + 0.5)) / n);
        const b = await grab(Math.max(0, t));
        if (b) frames.push(b);
      }
    } else {
      const b = await grab(0);
      if (b) frames.push(b);
    }
    return frames;
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function shelfScan(file) {
  if (!file) return;
  try {
    showSpinner("Reading the video…");
    const frames = await extractFrames(file);
    if (!frames.length) { alert("Couldn't get any frames from that video."); return; }
    showSpinner("Scanning the shelf for gems…");
    const fd = new FormData();
    frames.forEach((b, i) => fd.append("files", new File([b], `frame_${i}.jpg`, { type: "image/jpeg" })));
    const res = await api("/api/shelf-scan", { method: "POST", body: fd });
    renderShelfResult(res.items || []);
  } catch (e) {
    alert("Shelf scan error: " + e.message);
  } finally {
    hideSpinner();
  }
}
const shelfScanOnce = once("shelf", shelfScan);

function renderShelfResult(items) {
  const box = $("shelf-result");
  $("shop-result").classList.add("hidden");  // one result panel at a time
  if (!items.length) {
    box.innerHTML = `<p class="hint">Nothing jumped out as a clear resale win. Try a slower pan or better light — or scan individual items.</p>`;
    box.classList.remove("hidden");
    return;
  }
  const rows = items.map((it) =>
    `<li class="shelf-item">
       <div class="shelf-item-head">
         <span class="shelf-name">${escapeHtml(it.name)}</span>
         <span class="badge ${it.confidence}">${(it.confidence || "medium").toUpperCase()}</span>
       </div>
       ${it.reason ? `<div class="hint">${escapeHtml(it.reason)}</div>` : ""}
       ${it.location ? `<div class="shelf-loc">📍 ${escapeHtml(it.location)}</div>` : ""}
     </li>`).join("");
  box.innerHTML =
    `<p class="shelf-head">👀 ${items.length} item${items.length === 1 ? "" : "s"} worth a closer look:</p>
     <ul class="shelf-list">${rows}</ul>
     <p class="hint">Point your camera at one and tap <strong>📷 Scan an item</strong> for a full ID + price.</p>`;
  box.classList.remove("hidden");
}

// ---------- navigation ----------
function showView(view) {
  $("step-upload").classList.toggle("hidden", view !== "upload");
  $("step-preview").classList.toggle("hidden", view !== "preview");
  $("step-listings").classList.toggle("hidden", view !== "listings");
  $("step-bulk").classList.toggle("hidden", view !== "bulk");
  // Contextual nav buttons.
  $("nav-images").classList.toggle("hidden", view !== "preview");
  $("nav-edit").classList.toggle("hidden", !(view !== "preview" && state.listing));
}

// ---------- bulk mode ----------
async function bulkStart() {
  if (!state.files.length) return;
  const mode = (document.querySelector('input[name="bulk-mode"]:checked') || {}).value || "draft";
  if (mode === "live" && !confirm(
    "Auto-publish ALL detected items live to eBay? Each will go straight to your store.")) return;
  try {
    showSpinner("Uploading photos…");
    const prepped = await Promise.all(state.files.map(downscaleForUpload));
    const fd = new FormData();
    prepped.forEach((f) => fd.append("files", f));
    fd.append("mode", mode);
    fd.append("remove_bg", $("opt-remove-bg").checked ? "true" : "false");
    const { job_id } = await api("/api/bulk/upload", { method: "POST", body: fd });
    hideSpinner();
    showView("bulk");
    $("bulk-queue").innerHTML = "";
    $("bulk-queue-bar").classList.add("hidden");
    bulkPoll(job_id, mode);
  } catch (e) {
    hideSpinner();
    alert("Bulk upload failed: " + e.message);
  }
}

const BULK_PHASE_LABEL = {
  uploading: "Uploading…", optimizing: "Optimizing photos…",
  grouping: "Sorting photos into items…", identifying: "Identifying items…",
  done: "Done", none: "Working…",
};
async function bulkPoll(jobId, mode) {
  try {
    const job = await api(`/api/bulk/status/${jobId}`);
    const label = BULK_PHASE_LABEL[job.phase] || "Working…";
    let detail = "";
    if (job.phase === "identifying" && job.total_items)
      detail = ` (${job.current}/${job.total_items})`;
    else if (job.phase === "grouping" && job.total_photos)
      detail = ` (${job.total_photos} photos)`;
    $("bulk-progress").innerHTML = job.done
      ? "" : `<div class="spin-inline"></div><span>${escapeHtml(label)}${escapeHtml(detail)}</span>`;
    // Render items as they arrive (live-mode statuses update in place too).
    if (job.items && job.items.length) renderBulkQueue(job.items, mode);
    if (job.done) {
      if (job.error) $("bulk-progress").innerHTML = `<p class="hint">⚠ ${escapeHtml(job.error)}</p>`;
      else $("bulk-progress").innerHTML =
        `<p class="hint">✅ ${job.items.length} item${job.items.length === 1 ? "" : "s"} ${mode === "live" ? "processed" : "queued as drafts"}. Review below.</p>`;
      return;
    }
    setTimeout(() => bulkPoll(jobId, mode), 1500);
  } catch (e) {
    $("bulk-progress").innerHTML = `<p class="hint">⚠ Lost the bulk job: ${escapeHtml(e.message)}</p>`;
  }
}

function renderBulkQueue(items, mode) {
  state.bulkItems = items;
  const box = $("bulk-queue");
  box.innerHTML = "";
  // Only draft items are selectable/publishable from the queue.
  const anyDraft = items.some((it) => it.status === "draft");
  $("bulk-queue-bar").classList.toggle("hidden", !anyDraft);
  items.forEach((it) => box.appendChild(bulkCard(it)));
}

function bulkPublishSelected() {
  const cards = [...document.querySelectorAll("#bulk-queue .bulk-card")]
    .filter((c) => { const cb = c.querySelector(".bulk-check"); return cb && cb.checked; });
  bulkPublish(cards);
}
function bulkPublishAll() {
  bulkPublish([...document.querySelectorAll("#bulk-queue .bulk-card")]);
}

function bulkCard(it) {
  const card = document.createElement("div");
  card.className = "bulk-card status-" + it.status;
  card.dataset.session = it.session_id;
  const l = it.listing || {};
  const statusPill = {
    draft: `<span class="pill">draft</span>`,
    published: `<span class="pill ok">✓ live${it.listing_id ? " · " + escapeHtml(it.listing_id) : ""}</span>`,
    error: `<span class="pill warn">needs attention</span>`,
  }[it.status] || "";
  const checkbox = it.status === "draft"
    ? `<input type="checkbox" class="bulk-check" checked />` : "";
  const editable = it.status !== "error";
  card.innerHTML =
    `<div class="bulk-card-top">${checkbox}
       <img class="bulk-thumb" src="${escapeHtml(it.thumb)}?v=${Date.now()}" onerror="this.style.display='none'"/>
       ${statusPill}</div>` +
    (editable ? `<input class="bulk-title" type="text" value="${escapeHtml(l.title || it.title || "")}" placeholder="Title" />
       <div class="bulk-row">
         <input class="bulk-price" type="number" step="0.01" min="0" value="${l.price != null ? l.price : ""}" placeholder="Price" />
         <select class="bulk-cond"></select>
       </div>` : `<div class="hint">${escapeHtml(it.error || "Couldn't identify this item.")}</div>`) +
    `<div class="bulk-card-actions">
       ${editable ? `<button class="ghost bulk-open" type="button">Open full editor →</button>` : ""}
       ${it.status === "draft" ? `<button class="secondary bulk-pub-one" type="button">Publish</button>` : ""}
     </div>`;
  if (editable) {
    const condSel = card.querySelector(".bulk-cond");
    CONDITIONS.forEach((c) => {
      const o = document.createElement("option");
      o.value = c; o.textContent = c.replaceAll("_", " ");
      if (c === l.condition) o.selected = true;
      condSel.appendChild(o);
    });
    card.querySelector(".bulk-open").addEventListener("click", () => bulkOpen(it));
  }
  const pub = card.querySelector(".bulk-pub-one");
  if (pub) pub.addEventListener("click", () => bulkPublish([card]));
  return card;
}

// Persist a card's inline edits back onto the item's listing, then return it.
async function bulkSaveCard(card, it) {
  if (it.status === "error" || !it.listing) return it.listing;
  const l = { ...it.listing };
  l.title = card.querySelector(".bulk-title").value;
  const p = card.querySelector(".bulk-price").value;
  l.price = p === "" ? null : parseFloat(p);
  l.condition = card.querySelector(".bulk-cond").value;
  it.listing = l;
  await api(`/api/save/${it.session_id}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(l),
  });
  return l;
}

function bulkOpen(it) {
  // Load this bulk item into the normal single-item editor.
  state.sessionId = it.session_id;
  state.listing = it.listing;
  renderPreview({ confidence: "medium" });
  showView("preview");
}

async function bulkPublish(cards) {
  const targets = cards.filter((c) => c.dataset.session);
  if (!targets.length) { alert("Nothing selected to publish."); return; }
  let ok = 0, failed = 0;
  for (const card of targets) {
    const sid = card.dataset.session;
    const it = (state.bulkItems || []).find((x) => x.session_id === sid);
    if (!it || it.status !== "draft") continue;
    try {
      showSpinner(`Publishing ${ok + failed + 1}/${targets.length}…`);
      const listing = await bulkSaveCard(card, it);
      const res = await api("/api/publish", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid, listing, mode: "live" }),
      });
      if (res.published) { it.status = "published"; it.listing_id = res.listing_id; ok++; }
      else { it.status = "error"; it.error = res.message || "Publish blocked — open the full editor to fix."; failed++; }
    } catch (e) {
      it.status = "error"; it.error = e.message; failed++;
    }
  }
  hideSpinner();
  renderBulkQueue(state.bulkItems, "draft");
  alert(`Published ${ok} listing${ok === 1 ? "" : "s"}.` + (failed ? ` ${failed} need attention — see the queue.` : ""));
}

// ---------- my listings ----------
async function loadListings() {
  showView("listings");
  const grid = $("listings-grid");
  grid.innerHTML = "<p class='hint'>Loading…</p>";
  try {
    const res = await api("/api/listings");
    if (!res.db || !res.db.configured) {
      $("listings-note").textContent =
        "No database configured — set DATABASE_URL to save listing history.";
      grid.innerHTML = "";
      return;
    }
    if (!res.authed) {
      $("listings-note").textContent = "Log in to save and see your listings.";
      grid.innerHTML = "";
      openAuthModal();
      return;
    }
    $("listings-note").textContent = res.db && res.db.connected
      ? "" : "Database configured but unreachable.";
    renderListings(res.listings || []);
  } catch (e) {
    grid.innerHTML = `<p class="hint">Couldn't load listings: ${escapeHtml(e.message)}</p>`;
  }
}

function listingCard(it, inventory) {
  const l = it.listing || {};
  const thumb = (l.images && l.images[0]) ? `/media/${it.id}/optimized/${l.images[0]}` : "";
  const card = document.createElement("div");
  card.className = "listing-card" + (inventory ? " inventory" : "");
  const sub = inventory
    ? `Unlisted · ${l.price != null ? "≈$" + l.price : "no price yet"}`
    : `${escapeHtml(it.status)} · ${l.price != null ? "$" + l.price : "no price"}`;
  card.innerHTML =
    (thumb ? `<img src="${thumb}" onerror="this.style.display='none'"/>` : `<div class="noimg">no image</div>`) +
    `<div class="listing-meta">
       <strong>${escapeHtml(l.title || it.title || "(untitled)")}</strong>
       <span class="listing-sub">${sub}</span>
       ${inventory ? `<span class="list-it">Finish &amp; list →</span>` : ""}
     </div>`;
  card.addEventListener("click", () => openListing(it.id));
  return card;
}

function renderListings(items) {
  const grid = $("listings-grid");
  if (!items.length) {
    grid.innerHTML = "<p class='hint'>No saved listings yet. Scan an item in Shop mode, or start a new listing.</p>";
    return;
  }
  const inv = items.filter((it) => it.status === "unlisted");
  const rest = items.filter((it) => it.status !== "unlisted");
  grid.innerHTML = "";
  if (inv.length) {
    const h = document.createElement("h3"); h.className = "listings-section"; h.textContent = "📦 Unlisted inventory";
    const p = document.createElement("p"); p.className = "hint";
    p.textContent = "Items you bought in Shop mode. Open one to finish the details and publish.";
    grid.appendChild(h); grid.appendChild(p);
    inv.forEach((it) => grid.appendChild(listingCard(it, true)));
  }
  if (rest.length) {
    if (inv.length) {
      const h = document.createElement("h3"); h.className = "listings-section"; h.textContent = "🏷️ Listings";
      grid.appendChild(h);
    }
    rest.forEach((it) => grid.appendChild(listingCard(it, false)));
  }
}

async function openListing(id) {
  try {
    showSpinner("Loading listing…");
    const rec = await api(`/api/listings/${id}`);
    state.sessionId = rec.id;
    state.listing = rec.listing;
    renderPreview({ confidence: "medium" });
    showView("preview");
  } catch (e) {
    alert("Couldn't open listing: " + e.message);
  } finally {
    hideSpinner();
  }
}

function startNew() {
  if (state.listing && !confirm("Start a new listing? Your current draft will be cleared.")) return;
  state.sessionId = null;
  state.files = [];
  state.listing = null;
  renderThumbs();  // clears thumbs and revokes their object URLs
  $("file-input").value = "";
  $("btn-process").disabled = true;
  $("publish-result").classList.add("hidden");
  showView("upload");
}

// ---------- auth ----------
async function loadAuth() {
  try {
    const res = await api("/api/auth/me");
    state.user = res.user;
  } catch (e) { state.user = null; }
  renderAuthArea();
}

function renderAuthArea() {
  const area = $("auth-area");
  if (state.user) {
    area.innerHTML =
      `<span class="user-chip">👟 ${escapeHtml(state.user.email)}</span>` +
      `<button id="btn-logout" class="nav-btn" type="button">Log out</button>`;
    $("btn-logout").addEventListener("click", logout);
  } else {
    area.innerHTML = `<button id="btn-login" class="nav-btn" type="button">Log in</button>`;
    $("btn-login").addEventListener("click", openAuthModal);
  }
}

function openAuthModal() {
  $("auth-error").textContent = "";
  $("auth-overlay").classList.remove("hidden");
  setAuthMode("login");
}
function closeAuthModal() { $("auth-overlay").classList.add("hidden"); }

function setAuthMode(mode) {
  state.authMode = mode;
  $("tab-login").classList.toggle("active", mode === "login");
  $("tab-signup").classList.toggle("active", mode === "signup");
  $("auth-submit").textContent = mode === "login" ? "Log in" : "Create account";
  $("auth-error").textContent = "";
}

async function submitAuth() {
  const email = $("auth-email").value.trim();
  const password = $("auth-password").value;
  if (!email || !password) { $("auth-error").textContent = "Enter your email and password."; return; }
  try {
    showSpinner(state.authMode === "login" ? "Logging in…" : "Creating account…");
    const res = await api(`/api/auth/${state.authMode === "login" ? "login" : "signup"}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    state.user = res.user;
    closeAuthModal();
    renderAuthArea();
    loadEbayStatus();
    $("auth-email").value = ""; $("auth-password").value = "";
    // If the login modal was opened from an empty "My listings" view, populate
    // it now instead of leaving the user on a blank, still-logged-out page.
    if (!$("step-listings").classList.contains("hidden")) loadListings();
    // Resume a Shop-mode "Buy" that prompted login.
    if (pendingBuy) { pendingBuy = false; buyItem(); }
  } catch (e) {
    $("auth-error").textContent = e.message;
  } finally {
    hideSpinner();
  }
}

async function logout() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {}
  state.user = null;
  renderAuthArea();
  loadEbayStatus();
}

// The eBay status pill depends on two async fetches (health + per-user eBay
// status); render from state so whichever finishes last wins correctly. Create
// the pill on demand so a failed /api/health call can't hide eBay state.
function renderEbayPill() {
  let p = $("pill-ebay");
  if (!p) {
    const bar = $("status-bar");
    if (!bar) return;
    p = document.createElement("span");
    p.id = "pill-ebay";
    p.className = "pill";
    bar.appendChild(p);
  }
  if (state.ebayConnected) {
    p.textContent = state.ebayUsername
      ? `eBay: ${state.ebayUsername} ✓`
      : "eBay: connected (reconnect to show which account)";
    p.className = "pill ok";
    p.title = state.ebayUsername
      ? `Publishing goes to eBay account "${state.ebayUsername}"` +
        (state.ebayEmail ? ` (${state.ebayEmail})` : "") + `. Click "eBay connected" to manage.`
      : "Click 'eBay connected' to manage / reconnect and confirm the account";
  } else if (state.ebayConfigured) {
    p.textContent = "eBay: ready (server credentials)";
    p.className = "pill ok";
    p.title = "";
  } else {
    p.textContent = state.user
      ? "eBay: dry-run — click 'Connect eBay' to publish for real"
      : "eBay: dry-run — log in and connect eBay to publish for real";
    p.className = "pill warn";
    p.title = "Publish generates the exact eBay API payload without posting";
  }
}

// ---------- eBay connection ----------
async function loadEbayStatus() {
  const btn = $("nav-ebay");
  try {
    const s = await api("/api/ebay/status");
    state.ebayConnected = !!s.connected;
    state.ebayEnv = s.env || "";
    state.ebayUsername = s.username || "";
    state.ebayEmail = s.email || "";
    renderEbayPill();
    if (s.connected) {
      btn.textContent = state.ebayUsername ? `✓ eBay: ${state.ebayUsername}` : "✓ eBay connected";
      btn.style.background = "var(--green)";
      btn.style.color = "#fff";
      btn.title = "Click to see which account is linked, check payout, or switch";
      btn.dataset.connected = "1";
    } else {
      btn.textContent = "🔗 Connect eBay";
      btn.style.background = "";
      btn.style.color = "";
      btn.title = "";
      btn.dataset.connected = "0";
    }
    btn.dataset.ready = s.oauth_ready ? "1" : "0";
  } catch (e) { /* ignore */ }
}

function connectEbay() {
  if (!state.user) { openAuthModal(); return; }
  if ($("nav-ebay").dataset.connected === "1") { openEbayModal(); return; }
  if ($("nav-ebay").dataset.ready !== "1") {
    alert("eBay isn't configured on the server yet (needs EBAY_CLIENT_ID / SECRET / RUNAME).");
    return;
  }
  window.location.href = "/api/ebay/connect";
}

// ---------- profile (in Settings) ----------
async function loadProfile() {
  const box = $("profile-body");
  if (!state.user) { box.innerHTML = `<p class="hint">Log in to customize your profile.</p>`; return; }
  box.innerHTML = `<p class="hint">Loading…</p>`;
  try {
    const p = await api("/api/profile");
    renderProfileBody(p);
  } catch (e) {
    box.innerHTML = `<p class="hint">Couldn't load profile: ${escapeHtml(e.message)}</p>`;
  }
}

function renderProfileBody(p) {
  const box = $("profile-body");
  const e = p.ebay || {};
  const conn = e.connected
    ? `<p class="hint">eBay: <strong>${escapeHtml(e.username || "connected")}</strong>${e.email ? " · " + escapeHtml(e.email) : ""}</p>`
    : `<p class="hint">eBay not connected — connect it to auto-fill your details.</p>`;
  box.innerHTML =
    `<label class="settings-field">Display name
       <input type="text" id="profile-name" maxlength="80" value="${escapeHtml((p.user && p.user.display_name) || "")}" placeholder="e.g. your shop name" />
     </label>
     <p class="hint">Email: ${escapeHtml((p.user && p.user.email) || "")}</p>
     ${conn}
     <div class="profile-actions">
       <button id="profile-save" class="primary" type="button">Save profile</button>
       ${e.connected ? `<button id="profile-sync" class="secondary" type="button">↻ Sync from eBay</button>` : ""}
     </div>`;
  $("profile-save").addEventListener("click", saveProfile);
  if (e.connected && $("profile-sync")) $("profile-sync").addEventListener("click", syncProfileFromEbay);
}

async function saveProfile() {
  try {
    showSpinner("Saving profile…");
    const res = await api("/api/profile", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: $("profile-name").value.trim() }),
    });
    if (state.user) state.user.display_name = res.user.display_name;
    renderAuthArea();
    alert("Profile saved.");
  } catch (e) {
    alert("Couldn't save profile: " + e.message);
  } finally { hideSpinner(); }
}

async function syncProfileFromEbay() {
  try {
    showSpinner("Pulling your info from eBay…");
    const p = await api("/api/profile/sync-ebay", { method: "POST" });
    renderProfileBody(p);
    if (state.user && p.user) state.user.display_name = p.user.display_name;
    renderAuthArea();
    alert("Synced from eBay.");
  } catch (e) {
    alert("Sync failed: " + e.message);
  } finally { hideSpinner(); }
}

// ---------- listing settings (default eBay business policies) ----------
const POLICY_KINDS = [
  { key: "fulfillment", field: "fulfillment_policy_id", label: "Shipping policy" },
  { key: "payment", field: "payment_policy_id", label: "Payment policy" },
  { key: "return", field: "return_policy_id", label: "Return policy" },
];

async function openSettings(focus) {
  focus = (typeof focus === "string") ? focus : null;  // ignore click events
  const body = $("settings-body");
  $("settings-overlay").classList.remove("hidden");
  loadProfile();
  if (!state.user) {
    body.innerHTML = `<p class="hint">Log in and connect eBay to set your listing defaults.</p>`;
    return;
  }
  if (!state.ebayConnected) {
    body.innerHTML = `<p class="hint">Connect your eBay account first — your shipping, payment, and return templates come from there.</p>`;
    return;
  }
  body.innerHTML = `<p class="hint">Loading your eBay policies…</p>`;
  try {
    const data = await api("/api/ebay/policies");
    state.ebayPoliciesData = data;
    renderSettingsBody(data);
    if (focus === "postal") { const p = $("settings-postal"); if (p) { markFix(p); p.focus(); } }
    else if (focus === "policies") { document.querySelectorAll("#settings-body select[data-field]").forEach(markFix); }
  } catch (e) {
    body.innerHTML = `<p class="hint">Couldn't load policies: ${escapeHtml(e.message)}</p>`;
  }
}
function closeSettings() { $("settings-overlay").classList.add("hidden"); }

function renderSettingsBody(data) {
  const body = $("settings-body");
  const anyEmpty = POLICY_KINDS.some((k) => !(data.policies[k.key] || []).length);
  // Ship-from ZIP creates the eBay inventory location that publishing requires.
  let html = `<label class="settings-field">Ship-from ZIP code
    <input type="text" id="settings-postal" inputmode="numeric" placeholder="e.g. 90210"
      value="${escapeHtml(data.ship_from_postal || "")}" />
    <span class="hint">${data.location_set
      ? "✓ eBay ship-from location is set."
      : "⚠ Required to publish — eBay needs a location to ship from."}</span>
  </label>`;
  POLICY_KINDS.forEach(({ key, field, label }) => {
    const opts = data.policies[key] || [];
    const sel = data.selected[field] || "";
    const optionHtml = [`<option value="">— none —</option>`]
      .concat(opts.map((p) =>
        `<option value="${escapeHtml(p.id)}" ${p.id === sel ? "selected" : ""}>` +
        `${escapeHtml(p.name)}${p.summary ? " · " + escapeHtml(p.summary) : ""}</option>`))
      .join("");
    html += `<label class="settings-field">${label}
      <select data-field="${field}">${optionHtml}</select>
      ${opts.length ? "" : `<span class="hint">No ${label.toLowerCase()} on eBay yet.</span>`}
    </label>`;
  });
  if (anyEmpty) {
    html += `<p class="hint">Missing a policy? eBay requires shipping, payment &amp; return
      policies to publish. Create them on eBay, then reopen this. </p>
      <a class="settings-link" href="${escapeHtml(data.manage_url)}" target="_blank" rel="noopener">Manage eBay business policies →</a>`;
  }
  if (!data.location_set) {
    html += `<p class="hint">⚠ No eBay ship-from location yet. Enter your ZIP above and Save — we'll create it on eBay for you.</p>`;
  }
  html += `<button id="settings-save" class="primary" type="button">Save defaults</button>`;
  body.innerHTML = html;
  $("settings-save").addEventListener("click", saveSettings);
}

async function saveSettings() {
  const payload = {};
  $("settings-body").querySelectorAll("select[data-field]").forEach((s) => {
    payload[s.dataset.field] = s.value;
  });
  const postal = ($("settings-postal") ? $("settings-postal").value : "").trim();
  if (postal) payload.ship_from_postal = postal;
  try {
    showSpinner(postal ? "Saving & setting up eBay location…" : "Saving listing defaults…");
    await api("/api/ebay/policies", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    // refresh the cache and the publish-step summary
    state.ebayPoliciesData = null;
    closeSettings();
    if (state.listing) loadPublishDefaults();
    alert("Saved. These now apply to every listing you publish.");
  } catch (e) {
    alert("Couldn't save: " + e.message);
  } finally {
    hideSpinner();
  }
}

// Show the shipping/payment/return that will apply, on the publish step.
async function loadPublishDefaults() {
  const el = $("publish-defaults");
  if (!el) return;
  if (!state.ebayConnected) { el.innerHTML = ""; return; }
  try {
    const data = state.ebayPoliciesData || await api("/api/ebay/policies");
    state.ebayPoliciesData = data;
    const nameFor = (key, field) =>
      ((data.policies[key] || []).find((p) => p.id === data.selected[field]) || {}).name || "not set";
    el.innerHTML =
      `<div class="publish-defaults">Applies to this listing — ` +
      `<strong>Shipping:</strong> ${escapeHtml(nameFor("fulfillment", "fulfillment_policy_id"))} · ` +
      `<strong>Payment:</strong> ${escapeHtml(nameFor("payment", "payment_policy_id"))} · ` +
      `<strong>Returns:</strong> ${escapeHtml(nameFor("return", "return_policy_id"))} ` +
      `<button class="linklike" type="button" onclick="openSettings()">change</button></div>`;
  } catch (e) { el.innerHTML = ""; }
}

// ---------- eBay account modal (which account, payout, disconnect) ----------
function openEbayModal() {
  const info = $("ebay-acct-info");
  info.innerHTML = state.ebayUsername
    ? `Connected to eBay account <strong>${escapeHtml(state.ebayUsername)}</strong>` +
      (state.ebayEmail ? ` <span class="hint">(${escapeHtml(state.ebayEmail)})</span>` : "") +
      ` on <strong>${escapeHtml(state.ebayEnv || "production")}</strong>.`
    : `Connected, but this link was made before we could read the account name. ` +
      `<strong>Disconnect and reconnect</strong> to confirm which account it is.`;
  $("ebay-overlay").classList.remove("hidden");
}
function closeEbayModal() { $("ebay-overlay").classList.add("hidden"); }

async function disconnectEbay() {
  if (!confirm(
    "Disconnect this eBay account?\n\n" +
    "To connect a DIFFERENT account, first sign out of eBay in your browser " +
    "(or use a private window) so eBay lets you choose — otherwise it may " +
    "silently reconnect the same account.")) return;
  try {
    showSpinner("Disconnecting eBay…");
    await api("/api/ebay/disconnect", { method: "POST" });
    closeEbayModal();
    await loadEbayStatus();
    alert("Disconnected. Click 'Connect eBay' to link the account you want.");
  } catch (e) {
    alert("Couldn't disconnect: " + e.message);
  } finally {
    hideSpinner();
  }
}

async function checkEbayPayments() {
  showSpinner("Checking payout setup on eBay…");
  try {
    const s = await api("/api/ebay/payments-status");
    if (s.opted_in) {
      alert(`✅ Payments are set up on eBay (${s.env}): status ${s.status}. ` +
        "Bank/payout onboarding is complete — you can publish live listings.");
    } else if (s.error) {
      alert(`⚠️ Couldn't verify payments setup (${s.env}): ${s.error}\n${s.detail || ""}`);
    } else {
      alert(`⚠️ eBay (${s.env}) reports payments status "${s.status || "unknown"}". ` +
        "Finish payout setup in eBay Seller Hub → Payments (bank verification can take 1–2 days).");
    }
  } catch (e) {
    alert(`⚠️ Payments check failed: ${e.message}`);
  } finally {
    hideSpinner();
  }
}

function handleEbayRedirect() {
  const params = new URLSearchParams(window.location.search);
  const e = params.get("ebay");
  if (e === "connected") alert("✅ eBay connected! You can now publish real listings.");
  else if (e === "error") alert("⚠️ eBay connection failed. Please try again.");
  if (e) history.replaceState({}, "", window.location.pathname);
}

// ---------- step 2: preview ----------
// allowed: optional [{enum,label}] from eBay for the category. When present we
// show ONLY those (with eBay's own labels) so the seller can't pick a condition
// eBay rejects for the category (publish error 25021).
function renderConditionOptions(selected, allowed) {
  const sel = $("f-condition");
  sel.innerHTML = "";
  const opts = (allowed && allowed.length)
    ? allowed.map((c) => ({ value: c.enum, label: c.label || c.enum.replaceAll("_", " ") }))
    : CONDITIONS.map((c) => ({ value: c, label: c.replaceAll("_", " ") }));
  const hasSelected = opts.some((o) => o.value === selected);
  opts.forEach((o) => {
    const el = document.createElement("option");
    el.value = o.value; el.textContent = o.label;
    if (o.value === selected) el.selected = true;
    sel.appendChild(el);
  });
  // If the AI's condition isn't valid for this category, default to the first
  // allowed one so we never submit an invalid condition.
  if (!hasSelected && opts.length) sel.value = opts[0].value;
}

function renderSpecifics(specs) {
  const box = $("specifics");
  box.innerHTML = "";
  (specs || []).forEach((s) => addSpecificRow(s.name, s.value));
}

function addSpecificRow(name = "", value = "") {
  const box = $("specifics");
  const row = document.createElement("div");
  row.className = "specific-row";
  row.innerHTML = `
    <input type="text" placeholder="Name" value="${escapeHtml(name)}" />
    <input type="text" placeholder="Value" value="${escapeHtml(value)}" />
    <button class="ghost" type="button">✕</button>`;
  row.querySelector("button").addEventListener("click", () => row.remove());
  box.appendChild(row);
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderImages() {
  const box = $("opt-images");
  box.innerHTML = "";
  ((state.listing && state.listing.images) || []).forEach((name) => {
    const wrap = document.createElement("div");
    wrap.className = "opt-image";
    const img = document.createElement("img");
    // Cache-bust so a just-edited image shows the new version.
    img.src = `/media/${state.sessionId}/optimized/${name}?v=${Date.now()}`;
    const del = document.createElement("button");
    del.type = "button";
    del.className = "img-del-btn";
    del.title = "Delete this photo";
    del.textContent = "🗑";
    del.addEventListener("click", () => deleteImage(name));
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ghost edit-img-btn";
    btn.textContent = "🖌️ Clean up background";
    btn.addEventListener("click", () => openImageEditor(name));
    wrap.appendChild(del);
    wrap.appendChild(img);
    wrap.appendChild(btn);
    box.appendChild(wrap);
  });
}

async function deleteImage(name) {
  const imgs = (state.listing && state.listing.images) || [];
  if (imgs.length <= 1) {
    alert("A listing needs at least one photo — add another before deleting this one.");
    return;
  }
  if (!confirm("Delete this photo? This can't be undone.")) return;
  try {
    showSpinner("Deleting photo…");
    await api("/api/delete-image", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, name }),
    });
    state.listing.images = imgs.filter((n) => n !== name);
    renderImages();
  } catch (e) {
    alert("Couldn't delete the photo: " + e.message);
  } finally {
    hideSpinner();
  }
}

// ---------- background clean-up editor ----------
const editor = { name: null, painting: false, ctx: null };

function openImageEditor(name) {
  const canvas = $("editor-canvas");
  const ctx = canvas.getContext("2d");
  editor.name = name;
  editor.ctx = ctx;
  const img = new Image();
  img.onload = () => {
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    ctx.drawImage(img, 0, 0);
    $("editor-overlay").classList.remove("hidden");
  };
  img.onerror = () => alert("Couldn't load the image to edit.");
  // Same-origin so the canvas isn't tainted and toBlob() works.
  img.src = `/media/${state.sessionId}/optimized/${name}?v=${Date.now()}`;
}

function closeImageEditor() {
  $("editor-overlay").classList.add("hidden");
  editor.name = null;
  editor.painting = false;
}

// Map a pointer event to canvas pixel coordinates (canvas is displayed scaled).
function _canvasPoint(e) {
  const canvas = $("editor-canvas");
  const rect = canvas.getBoundingClientRect();
  const p = e.touches ? e.touches[0] : e;
  return {
    x: (p.clientX - rect.left) * (canvas.width / rect.width),
    y: (p.clientY - rect.top) * (canvas.height / rect.height),
  };
}

function _paintAt(e) {
  if (!editor.painting || !editor.ctx) return;
  const { x, y } = _canvasPoint(e);
  const canvas = $("editor-canvas");
  // Brush radius is in displayed pixels; scale to canvas pixels.
  const scale = canvas.width / canvas.getBoundingClientRect().width;
  const r = (parseInt($("editor-brush").value, 10) || 40) * scale / 2;
  editor.ctx.fillStyle = "#ffffff";
  editor.ctx.beginPath();
  editor.ctx.arc(x, y, r, 0, Math.PI * 2);
  editor.ctx.fill();
}

// Close a modal on backdrop click ONLY when the press STARTED on the backdrop.
// Without this, a press-drag-release gesture (painting in the editor, or
// drag-selecting text in a field) that releases over the backdrop fires a
// `click` on the overlay and destroys the modal + any unsaved work.
function bindBackdropClose(overlayId, closeFn) {
  const overlay = $(overlayId);
  if (!overlay) return;
  let downOnBackdrop = false;
  overlay.addEventListener("mousedown", (e) => { downOnBackdrop = e.target === overlay; });
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay && downOnBackdrop) closeFn();
    downOnBackdrop = false;
  });
}

function setupImageEditor() {
  const canvas = $("editor-canvas");
  const start = (e) => { editor.painting = true; _paintAt(e); e.preventDefault(); };
  const move = (e) => { if (editor.painting) { _paintAt(e); e.preventDefault(); } };
  const end = () => { editor.painting = false; };
  canvas.addEventListener("mousedown", start);
  canvas.addEventListener("mousemove", move);
  window.addEventListener("mouseup", end);
  canvas.addEventListener("touchstart", start, { passive: false });
  canvas.addEventListener("touchmove", move, { passive: false });
  canvas.addEventListener("touchend", end);
  $("editor-cancel").addEventListener("click", closeImageEditor);
  bindBackdropClose("editor-overlay", closeImageEditor);
  $("editor-reset").addEventListener("click", () => { if (editor.name) openImageEditor(editor.name); });
  $("editor-save").addEventListener("click", saveEditedImage);
}

async function saveEditedImage() {
  if (!state.sessionId || !editor.name) {
    alert("Couldn't save — lost track of which photo this is. Close and reopen the clean-up editor.");
    return;
  }
  const canvas = $("editor-canvas");
  const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.92));
  if (!blob) { alert("Couldn't export the edited image."); return; }
  try {
    showSpinner("Saving edited photo…");
    const fd = new FormData();
    // session/name as form fields (not URL path) so nothing falls through to
    // the static handler and 405s.
    fd.append("session_id", state.sessionId);
    fd.append("name", editor.name);
    fd.append("file", new File([blob], editor.name, { type: "image/jpeg" }));
    await api("/api/edit-image", { method: "POST", body: fd });
    closeImageEditor();
    renderImages();  // cache-busted src picks up the new version
  } catch (e) {
    alert("Save failed: " + e.message);
  } finally {
    hideSpinner();
  }
}

function renderMissingInfo(missing) {
  const box = $("missing-info");
  if (!missing || missing.length === 0) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="missing-banner"><strong>⚠ Please verify / fill in:</strong>
    <ul>${missing.map((m) => `<li>${escapeHtml(m)}</li>`).join("")}</ul></div>`;
}

// ---------- category-driven fields (conditions + required item specifics) ----------
// Read/write an item specific by name, keeping the specifics rows as the single
// source of truth so collectListing picks these up.
function getSpecificValue(name) {
  const rows = [...document.querySelectorAll("#specifics .specific-row")];
  const row = rows.find((r) => r.querySelectorAll("input")[0].value.trim().toLowerCase() === name.toLowerCase());
  return row ? row.querySelectorAll("input")[1].value.trim() : "";
}
function upsertSpecific(name, value) {
  const rows = [...document.querySelectorAll("#specifics .specific-row")];
  const row = rows.find((r) => r.querySelectorAll("input")[0].value.trim().toLowerCase() === name.toLowerCase());
  if (row) { row.querySelectorAll("input")[1].value = value; }
  else if (value) { addSpecificRow(name, value); }
}

// Fetch the category's valid conditions + required/recommended aspects and
// render inline fields, so the seller completes everything without leaving.
async function loadCategoryMeta() {
  const cid = ($("f-category-id").value || "").trim();
  const box = $("required-fields");
  if (!state.taxonomyConfigured || !cid) { box.innerHTML = ""; return; }
  try {
    const [cond, asp] = await Promise.all([
      api("/api/item-conditions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ category_id: cid }) }).catch(() => ({ conditions: [] })),
      api("/api/item-aspects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ category_id: cid }) }).catch(() => ({ aspects: [] })),
    ]);
    if (cond.conditions && cond.conditions.length) {
      renderConditionOptions($("f-condition").value, cond.conditions);
    }
    renderRequiredFields(asp.aspects || []);
  } catch (e) {
    box.innerHTML = "";
  }
}

function renderRequiredFields(aspects) {
  const box = $("required-fields");
  box.innerHTML = "";
  if (!aspects.length) return;
  // Show all required, plus up to 8 recommended, to keep it manageable.
  const required = aspects.filter((a) => a.required);
  const recommended = aspects.filter((a) => !a.required).slice(0, 8);
  const rows = required.concat(recommended);
  if (!rows.length) return;

  const wrap = document.createElement("div");
  wrap.className = "req-fields";
  wrap.innerHTML = `<strong>eBay item specifics for this category</strong>
    <p class="hint">Required fields must be filled to publish. Edit any value freely.</p>`;
  const IDENTIFIERS = ["upc", "ean", "isbn", "gtin"];
  rows.forEach((a) => {
    let cur = getSpecificValue(a.name);
    // Product identifiers eBay may require: default to the accepted
    // "Does Not Apply" sentinel so a thrifted item isn't blocked (overridable).
    if (!cur && IDENTIFIERS.includes(a.name.trim().toLowerCase())) {
      cur = "Does Not Apply";
      upsertSpecific(a.name, cur);
    }
    const field = document.createElement("div");
    field.className = "req-field";
    const req = a.required ? ' data-required="1"' : "";
    const badge = a.required ? `<span class="req-badge required">Required</span>` : `<span class="req-badge">Recommended</span>`;
    let control;
    if (a.mode === "SELECTION_ONLY" && a.values && a.values.length) {
      const opts = [`<option value="">— select —</option>`]
        .concat(a.values.map((v) => `<option value="${escapeHtml(v)}" ${v === cur ? "selected" : ""}>${escapeHtml(v)}</option>`))
        .join("");
      control = `<select data-aspect="${escapeHtml(a.name)}"${req}>${opts}</select>`;
    } else {
      control = `<input type="text" data-aspect="${escapeHtml(a.name)}"${req} value="${escapeHtml(cur)}" placeholder="${escapeHtml(a.name)}" />`;
    }
    field.innerHTML = `<label>${escapeHtml(a.name)} ${badge}</label>${control}`;
    wrap.appendChild(field);
  });
  box.appendChild(wrap);
  // Wire changes back into the specifics rows (single source of truth) and clear
  // the "needs fix" ring once a required field gets a value.
  wrap.querySelectorAll("[data-aspect]").forEach((el) => {
    const handler = () => {
      upsertSpecific(el.dataset.aspect, el.value.trim());
      if (el.value.trim()) el.classList.remove("needs-fix");
    };
    el.addEventListener("change", handler);
    el.addEventListener("input", handler);
  });
}

// Local pre-publish check so the seller fixes everything in one pass instead of
// round-tripping to eBay. Returns true if OK; otherwise highlights gaps and
// shows a fix panel. `forLive` applies the publish-only requirements.
function validateForPublish(forLive) {
  const missing = [];
  const clear = () => document.querySelectorAll(".needs-fix").forEach((el) => el.classList.remove("needs-fix"));
  clear();
  const flag = (el, label) => { if (el) markFix(el); missing.push(label); };

  if (!$("f-title").value.trim()) flag($("f-title"), "Title");
  if (!$("f-category-id").value.trim()) flag($("f-category-id"), "eBay category");
  const price = parseFloat($("f-price").value);
  if (!(price > 0)) flag($("f-price"), "Price greater than $0");
  const imgs = (state.listing && state.listing.images) || [];
  if (!imgs.length) missing.push("At least one photo");

  if (forLive) {
    const lb = parseFloat($("f-weight-lb").value) || 0;
    const oz = parseFloat($("f-weight-oz").value) || 0;
    if (lb + oz / 16 <= 0) flag($("f-weight-lb"), "Package weight");
  }
  // Required category item specifics still empty.
  document.querySelectorAll('#required-fields [data-required="1"]').forEach((el) => {
    if (!el.value.trim()) {
      el.classList.add("needs-fix");
      missing.push(el.dataset.aspect);
    }
  });

  if (!missing.length) return true;
  const panel = $("publish-issues");
  const uniq = [...new Set(missing)];
  panel.innerHTML =
    `<p class="fix-head">⚠️ Fill these in before publishing:</p>` +
    `<ul class="fix-list">${uniq.map((m) => `<li class="fix-item"><div class="fix-title">${escapeHtml(m)}</div></li>`).join("")}</ul>`;
  panel.classList.remove("hidden");
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  return false;
}

function renderPreview(result) {
  // Guard against a record with a null/absent listing (e.g. opening a saved
  // listing whose data didn't load) so we render an empty form, not a crash.
  const l = state.listing || {};
  $("f-title").value = l.title || "";
  $("f-subtitle").value = l.subtitle || "";
  $("f-brand").value = l.brand || "";
  $("f-price").value = l.price != null ? l.price : "";
  $("f-qty").value = l.quantity || 1;
  $("f-weight-lb").value = l.package_weight_lb || "";
  $("f-weight-oz").value = l.package_weight_oz || "";
  $("f-len").value = l.package_length_in || "";
  $("f-wid").value = l.package_width_in || "";
  $("f-hei").value = l.package_height_in || "";
  $("f-category").value = l.category_suggestion || "";
  $("f-category-id").value = l.category_id || "";
  $("f-condition-desc").value = l.condition_description || "";
  $("f-description").value = l.description || "";
  $("cur-label").textContent = l.currency || "USD";
  renderConditionOptions(l.condition);
  renderSpecifics(l.item_specifics);
  renderImages();
  renderMissingInfo(l.missing_info);
  $("required-fields").innerHTML = "";
  // Pull the category's valid conditions + required specifics (async, best-effort).
  loadCategoryMeta();
  $("cat-suggestions").innerHTML = "";
  // Clear any state carried over from a previous listing so it can't leak into
  // this one (stale eBay fix-panel, price comps, or "needs fix" field rings).
  $("price-suggestions").innerHTML = "";
  $("publish-issues").innerHTML = "";
  $("publish-issues").classList.add("hidden");
  $("publish-result").classList.add("hidden");
  clearFixHighlights();
  updateTitleCount();

  // confidence is rendered into a class + text; only ever trust the known set.
  const rawConf = (result && result.confidence) || "medium";
  const conf = ["low", "medium", "high"].includes(rawConf) ? rawConf : "medium";
  $("confidence").innerHTML =
    `AI confidence: <span class="badge ${conf}">${conf.toUpperCase()}</span>`;

  $("publish-note").textContent = canPublishLive()
    ? "Save as Draft keeps it here in Thryft; Publish Live posts it to your eBay account."
    : "Dry-run mode: no eBay connection yet, so we'll generate the exact API payload for you to inspect/use later.";
  loadPublishDefaults();
}

function updateTitleCount() {
  const v = $("f-title").value.length;
  $("title-count").textContent = `${v}/80`;
}

// Read the editable form back into a listing object.
function collectListing() {
  const specs = [...document.querySelectorAll(".specific-row")].map((row) => {
    const [n, v] = row.querySelectorAll("input");
    return { name: n.value.trim(), value: v.value.trim() };
  }).filter((s) => s.name);

  const base = state.listing || {};
  const price = $("f-price").value;
  const num = (id) => parseFloat($(id).value) || 0;
  return {
    ...base,
    title: $("f-title").value,
    subtitle: $("f-subtitle").value,
    brand: $("f-brand").value,
    price: price === "" ? null : parseFloat(price),
    quantity: parseInt($("f-qty").value || "1", 10),
    package_weight_lb: num("f-weight-lb"),
    package_weight_oz: num("f-weight-oz"),
    package_length_in: num("f-len"),
    package_width_in: num("f-wid"),
    package_height_in: num("f-hei"),
    category_suggestion: $("f-category").value,
    category_id: $("f-category-id").value,
    condition: $("f-condition").value,
    condition_description: $("f-condition-desc").value,
    description: $("f-description").value,
    item_specifics: specs,
    images: base.images || [],
    currency: base.currency || "USD",
  };
}

async function suggestCategories() {
  const box = $("cat-suggestions");
  if (!state.taxonomyConfigured) {
    box.innerHTML =
      `<p class="hint">Automatic categories need EBAY_CLIENT_ID / EBAY_CLIENT_SECRET in .env. ` +
      `You can still enter a category ID manually.</p>`;
    return;
  }
  const l = collectListing();
  const query = [l.brand, l.title, l.category_suggestion].filter(Boolean).join(" ").trim();
  if (!query) { box.innerHTML = `<p class="hint">Add a title or brand first.</p>`; return; }
  try {
    showSpinner("Resolving eBay categories…");
    const res = await api("/api/category-suggestions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, limit: 5 }),
    });
    renderCategorySuggestions(res.suggestions || []);
  } catch (e) {
    box.innerHTML = `<p class="hint">Couldn't fetch categories: ${escapeHtml(e.message)}</p>`;
  } finally {
    hideSpinner();
  }
}

function renderCategorySuggestions(suggestions) {
  const box = $("cat-suggestions");
  box.innerHTML = "";
  if (suggestions.length === 0) {
    box.innerHTML = `<p class="hint">No category matches found. Try editing the title.</p>`;
    return;
  }
  const current = $("f-category-id").value.trim();
  suggestions.forEach((s) => {
    const row = document.createElement("div");
    row.className = "cat-suggestion" + (s.category_id === current ? " chosen" : "");
    row.innerHTML =
      `<span class="cat-path">${escapeHtml(s.path || s.category_name)}</span>` +
      `<span class="cat-id">#${escapeHtml(s.category_id)}</span>`;
    row.addEventListener("click", () => {
      $("f-category-id").value = s.category_id;
      $("f-category").value = s.path || s.category_name;
      [...box.children].forEach((c) => c.classList.remove("chosen"));
      row.classList.add("chosen");
      loadCategoryMeta();  // refresh valid conditions + required fields
    });
    box.appendChild(row);
  });
}

async function checkMarketPrice() {
  const box = $("price-suggestions");
  if (!state.taxonomyConfigured) {
    box.innerHTML = `<p class="hint">Price check needs EBAY_CLIENT_ID / EBAY_CLIENT_SECRET on the server.</p>`;
    return;
  }
  const l = collectListing();
  const query = [l.brand, l.title].filter(Boolean).join(" ").trim();
  if (!query) { box.innerHTML = `<p class="hint">Add a title or brand first.</p>`; return; }
  try {
    showSpinner("Checking live eBay prices…");
    const data = await api("/api/price-suggestions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        category_id: l.category_id || null,
        condition: l.condition || null,
      }),
    });
    renderPriceSuggestions(data);
  } catch (e) {
    box.innerHTML = `<p class="hint">Couldn't check prices: ${escapeHtml(e.message)}</p>`;
  } finally {
    hideSpinner();
  }
}

function renderPriceSuggestions(data) {
  const box = $("price-suggestions");
  box.innerHTML = "";
  const s = data.suggestion;
  if (!s) {
    box.innerHTML = `<p class="hint">No comparable listings found — try a simpler title or set a category first.</p>`;
    return;
  }
  const usePrice = (row, price) => {
    $("f-price").value = Number(price).toFixed(2);
    [...box.querySelectorAll(".cat-suggestion")].forEach((c) => c.classList.remove("chosen"));
    row.classList.add("chosen");
  };
  data.sources.forEach((src) => {
    const head = document.createElement("div");
    head.className = "cat-suggestion";
    head.innerHTML =
      `<span class="cat-path"><strong>${escapeHtml(src.label)}</strong> — median of ` +
      `${src.count} listings (typical range $${src.low}–$${src.high}). Click to use.</span>` +
      `<span class="cat-id">$${src.estimate}</span>`;
    head.addEventListener("click", () => usePrice(head, src.estimate));
    box.appendChild(head);
    (src.sample || []).forEach((c) => {
      const row = document.createElement("div");
      row.className = "cat-suggestion";
      row.innerHTML =
        `<span class="cat-path">${escapeHtml(c.title)}` +
        (c.condition ? ` <em>(${escapeHtml(c.condition)})</em>` : "") +
        (c.url ? ` <a href="${escapeHtml(c.url)}" target="_blank" rel="noopener">view →</a>` : "") +
        `</span><span class="cat-id">$${c.price}</span>`;
      row.addEventListener("click", (e) => {
        if (e.target.tagName === "A") return; // let the eBay link work
        usePrice(row, c.price);
      });
      box.appendChild(row);
    });
    if (src.search_url) {
      const more = document.createElement("p");
      more.className = "hint";
      more.innerHTML = `<a href="${escapeHtml(src.search_url)}" target="_blank" rel="noopener">See all comparable listings on eBay →</a>`;
      box.appendChild(more);
    }
  });
  if (!s.sold_data) {
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = "These are asking prices (what sellers want), not sold prices — pricing a little under the median usually sells faster.";
    box.appendChild(note);
  }
}

async function refine() {
  const prompt = $("refine-input").value.trim();
  if (!prompt) return;
  try {
    showSpinner("Refining with AI…");
    state.listing = collectListing(); // capture manual edits first
    const updated = await api("/api/refine", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, listing: state.listing, prompt }),
    });
    state.listing = updated;
    renderPreview({ confidence: "medium" });
    $("refine-input").value = "";
  } catch (e) {
    alert("Refine error: " + e.message);
  } finally {
    hideSpinner();
  }
}

async function publish(mode) {
  // Pre-publish validation: catch missing required fields locally so the seller
  // fixes them in one pass instead of round-tripping to eBay. Drafts are allowed
  // to be incomplete, so only gate live publishes.
  if (mode === "live" && !validateForPublish(true)) return;
  try {
    showSpinner(mode === "live" ? "Publishing live…" : "Saving draft…");
    clearFixHighlights();
    state.listing = collectListing();
    const result = await api("/api/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, listing: state.listing, mode }),
    });
    const out = $("publish-result");
    $("publish-issues").classList.add("hidden");
    out.classList.remove("hidden");
    if (result.dry_run) {
      out.textContent =
        `✅ ${result.message}\nSaved payload: ${result.export_path}\n\n` +
        JSON.stringify(result.payload, null, 2);
    } else if (result.draft) {
      out.textContent = `✅ ${result.message}`;
    } else if (result.ebay_draft) {
      out.textContent = `✅ ${result.message || "Saved as a draft on eBay."}`;
    } else if (result.error) {
      out.classList.add("hidden");
      renderPublishIssues(result);   // friendly "what to fix" panel + field highlight
    } else if (result.published && result.listing_id) {
      out.textContent =
        `✅ Published live to eBay! Listing ID: ${result.listing_id}\n` +
        `View it in your eBay account under Selling → Active.`;
    } else {
      out.textContent =
        `✅ ${mode === "live" ? "Published live!" : "Done"}\n` +
        JSON.stringify(result, null, 2);
    }
  } catch (e) {
    alert("Publish error: " + e.message);
  } finally {
    hideSpinner();
  }
}

// ---------- publish error → "what to fix" ----------
function clearFixHighlights() {
  document.querySelectorAll(".needs-fix").forEach((el) => el.classList.remove("needs-fix"));
}
function markFix(el) { if (el) el.classList.add("needs-fix"); }

// Highlight (and optionally jump to) the field eBay flagged. `soft` just marks
// it without scrolling/opening — used to pre-highlight the top issue.
function highlightFix(target, soft) {
  clearFixHighlights();
  const jump = (el) => { if (el && !soft) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.focus({ preventScroll: true }); } };
  switch (target) {
    case "category": markFix($("f-category-id")); markFix($("f-category")); jump($("f-category-id")); break;
    case "price": markFix($("f-price")); jump($("f-price")); break;
    case "weight": markFix($("f-weight-lb")); markFix($("f-weight-oz")); jump($("f-weight-lb")); break;
    case "title": markFix($("f-title")); jump($("f-title")); break;
    case "description": markFix($("f-description")); jump($("f-description")); break;
    case "specifics":
      markFix($("specifics"));
      if (!soft) $("specifics").scrollIntoView({ behavior: "smooth", block: "center" });
      break;
    case "photos":
      if (!soft) { showView("upload"); alert("Re-upload your photos, then Publish Live again."); }
      break;
    case "location": if (!soft) openSettings("postal"); break;
    case "policies": if (!soft) openSettings("policies"); break;
    default: break; // generic — nothing to point at
  }
}

function renderPublishIssues(result) {
  clearFixHighlights();
  const panel = $("publish-issues");
  const issues = (result.issues && result.issues.length)
    ? result.issues
    : [{ target: "generic", title: result.message || "eBay rejected the listing", fix: result.detail || "" }];

  const items = issues.map((it) => {
    const canFix = it.target && it.target !== "generic";
    return `<li class="fix-item">
      <div class="fix-title">${escapeHtml(it.title)}</div>
      <div class="fix-how">${escapeHtml(it.fix || "")}</div>
      ${canFix ? `<button class="fix-btn" data-target="${escapeHtml(it.target)}">Fix this →</button>` : ""}
    </li>`;
  }).join("");

  const raw = result.detail
    ? `<details class="fix-raw"><summary>eBay's exact message</summary><pre>${
        escapeHtml(typeof result.detail === "string" ? result.detail : JSON.stringify(result.detail, null, 2))
      }</pre></details>`
    : "";

  panel.innerHTML =
    `<p class="fix-head">⚠️ ${escapeHtml(result.message || "eBay couldn't publish this yet")}</p>` +
    `<ul class="fix-list">${items}</ul>${raw}`;
  panel.classList.remove("hidden");
  panel.querySelectorAll("button[data-target]").forEach((b) =>
    b.addEventListener("click", () => highlightFix(b.dataset.target)));

  // Pre-highlight the first fixable field so it's obvious where to look.
  const first = issues.find((x) => x.target && x.target !== "generic");
  if (first) highlightFix(first.target, true);
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ---------- wire up ----------
function init() {
  setupDropzone();
  // Clear a field's "needs fix" ring as soon as the user edits it.
  document.addEventListener("input", (e) => {
    if (e.target && e.target.classList && e.target.classList.contains("needs-fix")) {
      e.target.classList.remove("needs-fix");
    }
  });
  setupImageEditor();
  // Render + wire the auth button synchronously so tapping "Log in" works
  // immediately; loadAuth() re-renders it once /api/auth/me resolves.
  renderAuthArea();
  loadHealth();
  loadAuth();
  loadEbayStatus();
  handleEbayRedirect();
  $("nav-ebay").addEventListener("click", connectEbay);
  // Listing settings modal
  $("nav-settings").addEventListener("click", openSettings);
  $("settings-close").addEventListener("click", closeSettings);
  bindBackdropClose("settings-overlay", closeSettings);
  // eBay account modal
  $("ebay-close").addEventListener("click", closeEbayModal);
  $("ebay-check-payout").addEventListener("click", () => { closeEbayModal(); checkEbayPayments(); });
  $("ebay-disconnect").addEventListener("click", disconnectEbay);
  bindBackdropClose("ebay-overlay", closeEbayModal);
  // Auth modal wiring (the login/logout button itself is wired by
  // renderAuthArea, which replaces it on every auth change)
  $("auth-close").addEventListener("click", closeAuthModal);
  $("tab-login").addEventListener("click", () => setAuthMode("login"));
  $("tab-signup").addEventListener("click", () => setAuthMode("signup"));
  $("auth-submit").addEventListener("click", submitAuth);
  $("auth-password").addEventListener("keydown", (e) => { if (e.key === "Enter") submitAuth(); });
  bindBackdropClose("auth-overlay", closeAuthModal);
  const processImagesOnce = once("process", processImages);
  const refineOnce = once("refine", refine);
  const publishOnce = once("publish", publish);  // one guard covers draft+live
  $("btn-process").addEventListener("click", processImagesOnce);
  $("btn-refine").addEventListener("click", refineOnce);
  $("btn-add-specific").addEventListener("click", () => addSpecificRow());
  $("btn-suggest-cat").addEventListener("click", suggestCategories);
  $("btn-price-check").addEventListener("click", checkMarketPrice);
  $("btn-draft").addEventListener("click", () => publishOnce("draft"));
  $("btn-live").addEventListener("click", () => publishOnce("live"));
  $("f-title").addEventListener("input", updateTitleCount);
  // Re-pull valid conditions + required specifics when the category id is edited.
  $("f-category-id").addEventListener("change", loadCategoryMeta);
  $("refine-input").addEventListener("keydown", (e) => { if (e.key === "Enter") refineOnce(); });
  // Nav buttons
  $("nav-new").addEventListener("click", startNew);
  $("nav-listings").addEventListener("click", loadListings);
  $("nav-images").addEventListener("click", () => showView("upload"));
  $("nav-edit").addEventListener("click", () => showView("preview"));
  $("nav-restart").addEventListener("click", () => location.reload());
  // Shop / Sell mode toggle + Shop-mode capture inputs.
  $("mode-shop").addEventListener("click", () => setMode("shop"));
  $("mode-sell").addEventListener("click", () => setMode("sell"));
  $("shop-input").addEventListener("change", (e) => shopScanOnce(e.target.files[0]));
  $("shelf-input").addEventListener("change", (e) => shelfScanOnce(e.target.files[0]));
  // Bulk mode
  $("opt-bulk").addEventListener("change", (e) => {
    $("bulk-options").classList.toggle("hidden", !e.target.checked);
    $("btn-process").textContent = e.target.checked ? "Sort & create listings →" : "Optimize & Identify →";
  });
  $("bulk-publish-selected").addEventListener("click", bulkPublishSelected);
  $("bulk-publish-all").addEventListener("click", bulkPublishAll);
  $("bulk-select-all").addEventListener("change", (e) => {
    document.querySelectorAll("#bulk-queue .bulk-check").forEach((c) => { c.checked = e.target.checked; });
  });
  showView("upload");
}

init();
