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
};

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

// ---------- navigation ----------
function showView(view) {
  $("step-upload").classList.toggle("hidden", view !== "upload");
  $("step-preview").classList.toggle("hidden", view !== "preview");
  $("step-listings").classList.toggle("hidden", view !== "listings");
  // Contextual nav buttons.
  $("nav-images").classList.toggle("hidden", view !== "preview");
  $("nav-edit").classList.toggle("hidden", !(view !== "preview" && state.listing));
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

function renderListings(items) {
  const grid = $("listings-grid");
  if (!items.length) {
    grid.innerHTML = "<p class='hint'>No saved listings yet. Create one to see it here.</p>";
    return;
  }
  grid.innerHTML = "";
  items.forEach((it) => {
    const l = it.listing || {};
    const thumb = (l.images && l.images[0])
      ? `/media/${it.id}/optimized/${l.images[0]}` : "";
    const card = document.createElement("div");
    card.className = "listing-card";
    card.innerHTML =
      (thumb ? `<img src="${thumb}" onerror="this.style.display='none'"/>` : `<div class="noimg">no image</div>`) +
      `<div class="listing-meta">
         <strong>${escapeHtml(l.title || it.title || "(untitled)")}</strong>
         <span class="listing-sub">${escapeHtml(it.status)} · ${l.price != null ? "$" + l.price : "no price"}</span>
       </div>`;
    card.addEventListener("click", () => openListing(it.id));
    grid.appendChild(card);
  });
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
    if (focus === "postal") { markFix($("settings-postal")); if ($("settings-postal")) $("settings-postal").focus(); }
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
function renderConditionOptions(selected) {
  const sel = $("f-condition");
  sel.innerHTML = "";
  CONDITIONS.forEach((c) => {
    const o = document.createElement("option");
    o.value = c; o.textContent = c.replaceAll("_", " ");
    if (c === selected) o.selected = true;
    sel.appendChild(o);
  });
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
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ghost edit-img-btn";
    btn.textContent = "🖌️ Clean up background";
    btn.addEventListener("click", () => openImageEditor(name));
    wrap.appendChild(img);
    wrap.appendChild(btn);
    box.appendChild(wrap);
  });
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
  $("refine-input").addEventListener("keydown", (e) => { if (e.key === "Enter") refineOnce(); });
  // Nav buttons
  $("nav-new").addEventListener("click", startNew);
  $("nav-listings").addEventListener("click", loadListings);
  $("nav-images").addEventListener("click", () => showView("upload"));
  $("nav-edit").addEventListener("click", () => showView("preview"));
  $("nav-restart").addEventListener("click", () => location.reload());
  showView("upload");
}

init();
