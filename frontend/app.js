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
  ebayConfigured: false,
  taxonomyConfigured: false,
};

// ---------- helpers ----------
function showSpinner(text) {
  $("spinner-text").textContent = text || "Working…";
  $("spinner").classList.remove("hidden");
}
function hideSpinner() { $("spinner").classList.add("hidden"); }

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
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
    const missing = h.ebay_missing || [];
    const ebayLabel = h.ebay_configured
      ? `eBay: ${h.ebay_env} ready`
      : `eBay: dry-run (missing: ${missing.length ? missing.join(", ") : "credentials"})`;
    bar.appendChild(pill(ebayLabel, h.ebay_configured,
      h.ebay_configured ? "" : "Set these as env vars / Fly secrets to publish for real"));
    bar.appendChild(pill(
      h.taxonomy_configured ? "Categories: auto" : "Categories: manual",
      h.taxonomy_configured));
  } catch (e) { /* ignore */ }
}

// ---------- step 1: upload ----------
function renderThumbs() {
  const box = $("thumbs");
  box.innerHTML = "";
  state.files.forEach((f) => {
    const img = document.createElement("img");
    img.src = URL.createObjectURL(f);
    box.appendChild(img);
  });
  $("btn-process").disabled = state.files.length === 0;
}

function addFiles(fileList) {
  for (const f of fileList) {
    if (f.type.startsWith("image/")) state.files.push(f);
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

async function processImages() {
  try {
    showSpinner("Optimizing images…");
    const fd = new FormData();
    state.files.forEach((f) => fd.append("files", f));
    const up = await api("/api/upload", { method: "POST", body: fd });
    state.sessionId = up.session_id;

    showSpinner("Identifying with AI lens…");
    const result = await api(`/api/identify/${state.sessionId}`, { method: "POST" });
    state.listing = result.listing;
    renderPreview(result);
    $("step-upload").classList.add("hidden");
    $("step-preview").classList.remove("hidden");
  } catch (e) {
    alert("Error: " + e.message);
  } finally {
    hideSpinner();
  }
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
  (state.listing.images || []).forEach((name) => {
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
  const l = state.listing;
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

  $("publish-note").textContent = state.ebayConfigured
    ? "Connected to eBay. Drafts create an unpublished offer; Live publishes it."
    : "Dry-run mode: no eBay credentials yet, so we'll generate the exact API payload for you to inspect/use later.";
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

  const price = $("f-price").value;
  return {
    ...state.listing,
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
    images: state.listing.images,
    currency: state.listing.currency || "USD",
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
  loadHealth();
  $("btn-process").addEventListener("click", processImages);
  $("btn-refine").addEventListener("click", refine);
  $("btn-add-specific").addEventListener("click", () => addSpecificRow());
  $("btn-suggest-cat").addEventListener("click", suggestCategories);
  $("btn-draft").addEventListener("click", () => publish("draft"));
  $("btn-live").addEventListener("click", () => publish("live"));
  $("f-title").addEventListener("input", updateTitleCount);
  $("refine-input").addEventListener("keydown", (e) => { if (e.key === "Enter") refine(); });
}

init();
