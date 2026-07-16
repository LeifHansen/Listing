// A listing template captures the LOGISTICAL fields for a kind of item
// (category, condition, brand, package size, shipping service, boilerplate
// item specifics) so a seller can start a new listing pre-filled. The AI
// still writes the title, description, and price per item.

// The listing fields a template snapshots.
export function templateFromListing(listing) {
  const l = listing || {};
  return {
    category_id: l.category_id || "",
    category_name: l.category_name || "",
    condition: l.condition || "",
    brand: l.brand || "",
    package_weight_lb: l.package_weight_lb || "",
    package_weight_oz: l.package_weight_oz || "",
    package_length_in: l.package_length_in || "",
    package_width_in: l.package_width_in || "",
    package_height_in: l.package_height_in || "",
    shipping_service: l.shipping_service || "",
    // Boilerplate specifics: keep the names (and any fixed values) to prompt
    // the AI/seller; per-item values still get filled from the photos.
    item_specifics: (l.item_specifics || [])
      .filter((s) => s && s.name)
      .map((s) => ({ name: s.name, value: s.value || "" })),
  };
}

// Overlay a template's logistics onto a freshly-identified listing. The
// template is authoritative for the item TYPE (category, condition, brand,
// package, shipping); item specifics are merged — the AI's per-item values
// win, the template contributes any names the AI didn't produce.
export function applyTemplate(listing, tmpl) {
  const d = (tmpl && tmpl.data) || null;
  if (!d) return listing;
  const out = { ...listing };
  const take = (k) => { if (d[k] !== undefined && d[k] !== "" && d[k] !== null) out[k] = d[k]; };
  [
    "category_id", "category_name", "condition", "brand",
    "package_weight_lb", "package_weight_oz",
    "package_length_in", "package_width_in", "package_height_in",
    "shipping_service",
  ].forEach(take);

  const aiSpecs = listing.item_specifics || [];
  const seen = new Set(aiSpecs.map((s) => (s.name || "").toLowerCase()));
  const extra = (d.item_specifics || [])
    .filter((s) => s.name && !seen.has(s.name.toLowerCase()));
  out.item_specifics = [...aiSpecs.map((s) => ({ ...s })), ...extra.map((s) => ({ ...s }))];
  return out;
}
