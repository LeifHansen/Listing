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

function addFiles(fileList) {
  for (const f of fileList) {
    if (!f.type.startsWith("image/")) continue;
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
// status); render from state so whichever finishes last wins correctly.
function renderEbayPill() {
  const p = $("pill-ebay");
  if (!p) return;
  if (state.ebayConnected) {
    p.textContent = `eBay: your account connected (${state.ebayEnv || "production"})`;
    p.className = "pill ok";
    p.title = "Publishing goes to your connected eBay account";
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
    renderEbayPill();
    if (s.connected) {
      btn.textContent = "✓ eBay connected";
      btn.style.background = "var(--green)";
      btn.style.color = "#fff";
      btn.title = "Click to check payout (bank account) setup on eBay";
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
  if ($("nav-ebay").dataset.connected === "1") { checkEbayPayments(); return; }
  if ($("nav-ebay").dataset.ready !== "1") {
    alert("eBay isn't configured on the server yet (needs EBAY_CLIENT_ID / SECRET / RUNAME).");
    return;
  }
  window.location.href = "/api/ebay/connect";
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
  (specs || []).forEach((s, i) => addSpecificRow(s.name, s.value));
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
    const img = document.createElement("img");
    img.src = `/media/${state.sessionId}/optimized/${name}`;
    box.appendChild(img);
  });
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
  updateTitleCount();

  const conf = (result && result.confidence) || "medium";
  $("confidence").innerHTML =
    `AI confidence: <span class="badge ${conf}">${conf.toUpperCase()}</span>`;

  $("publish-note").textContent = canPublishLive()
    ? "Connected to eBay. Drafts create an unpublished offer; Live publishes it."
    : "Dry-run mode: no eBay connection yet, so we'll generate the exact API payload for you to inspect/use later.";
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
  return {
    ...base,
    title: $("f-title").value,
    subtitle: $("f-subtitle").value,
    brand: $("f-brand").value,
    price: price === "" ? null : parseFloat(price),
    quantity: parseInt($("f-qty").value || "1", 10),
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
    state.listing = collectListing();
    const result = await api("/api/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, listing: state.listing, mode }),
    });
    const out = $("publish-result");
    out.classList.remove("hidden");
    out.textContent = JSON.stringify(result, null, 2);
    if (result.dry_run) {
      out.textContent =
        `✅ ${result.message}\nSaved payload: ${result.export_path}\n\n` +
        JSON.stringify(result.payload, null, 2);
    } else if (result.error) {
      out.textContent = `❌ ${result.message}\n${result.detail || ""}`;
    } else {
      out.textContent =
        `✅ ${mode === "live" ? "Published live!" : "Draft created!"}\n` +
        JSON.stringify(result, null, 2);
    }
  } catch (e) {
    alert("Publish error: " + e.message);
  } finally {
    hideSpinner();
  }
}

// ---------- wire up ----------
function init() {
  setupDropzone();
  // Render + wire the auth button synchronously so tapping "Log in" works
  // immediately; loadAuth() re-renders it once /api/auth/me resolves.
  renderAuthArea();
  loadHealth();
  loadAuth();
  loadEbayStatus();
  handleEbayRedirect();
  $("nav-ebay").addEventListener("click", connectEbay);
  // Auth modal wiring (the login/logout button itself is wired by
  // renderAuthArea, which replaces it on every auth change)
  $("auth-close").addEventListener("click", closeAuthModal);
  $("tab-login").addEventListener("click", () => setAuthMode("login"));
  $("tab-signup").addEventListener("click", () => setAuthMode("signup"));
  $("auth-submit").addEventListener("click", submitAuth);
  $("auth-password").addEventListener("keydown", (e) => { if (e.key === "Enter") submitAuth(); });
  $("auth-overlay").addEventListener("click", (e) => { if (e.target === $("auth-overlay")) closeAuthModal(); });
  $("btn-process").addEventListener("click", processImages);
  $("btn-refine").addEventListener("click", refine);
  $("btn-add-specific").addEventListener("click", () => addSpecificRow());
  $("btn-suggest-cat").addEventListener("click", suggestCategories);
  $("btn-draft").addEventListener("click", () => publish("draft"));
  $("btn-live").addEventListener("click", () => publish("live"));
  $("f-title").addEventListener("input", updateTitleCount);
  $("refine-input").addEventListener("keydown", (e) => { if (e.key === "Enter") refine(); });
  // Nav buttons
  $("nav-new").addEventListener("click", startNew);
  $("nav-listings").addEventListener("click", loadListings);
  $("nav-images").addEventListener("click", () => showView("upload"));
  $("nav-edit").addEventListener("click", () => showView("preview"));
  $("nav-restart").addEventListener("click", () => location.reload());
  showView("upload");
}

init();
