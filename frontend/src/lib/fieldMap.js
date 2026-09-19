/* The field alignment map, as the browser sees it.

   One listing record, three vocabularies — what a field is here, what it is
   on eBay, what it becomes on Etsy, and how it crosses over: "shared" is
   copied, "derived" is computed by the named rule, "manual" is a question
   Etsy asks that eBay never did, "absent" is a field Etsy does not have.

   This is a COPY of backend/marketplaces/field_map.py, and a backend test
   (test_the_field_map_is_the_same_on_both_sides) reads this file and fails
   when the two drift — so edit the Python, then paste the JSON here. The
   array below is parsed as JSON by that test: double quotes, no trailing
   commas, no comments inside it. */

export const FIELD_MAP = [
  {
    "key": "title",
    "label": "Title",
    "fill": "derived",
    "derive": "clean_title",
    "required_for_etsy": true,
    "preflight_target": "title",
    "note": "eBay allows 80 characters and capitals; Etsy 140, no $ ^ ` and few words in capitals — tidied on the way, shown before sending"
  },
  {
    "key": "description",
    "label": "Description",
    "fill": "derived",
    "derive": "strip_html+condition",
    "required_for_etsy": true,
    "preflight_target": "description",
    "note": "Plain text on Etsy; the condition is written in, because Etsy has no condition field"
  },
  {
    "key": "price",
    "label": "Price",
    "fill": "shared",
    "derive": "",
    "required_for_etsy": true,
    "preflight_target": "price",
    "note": "Etsy's floor is 0.20 in the shop's currency; on a revise it travels through the inventory record"
  },
  {
    "key": "currency",
    "label": "Currency",
    "fill": "shared",
    "derive": "",
    "required_for_etsy": false,
    "preflight_target": "price",
    "note": "An Etsy shop prices in one currency; a listing in another is a warning"
  },
  {
    "key": "quantity",
    "label": "Quantity",
    "fill": "shared",
    "derive": "",
    "required_for_etsy": true,
    "preflight_target": "",
    "note": "One number; a listing with variations is refused for Etsy"
  },
  {
    "key": "photos",
    "label": "Photos",
    "fill": "shared",
    "derive": "",
    "required_for_etsy": true,
    "preflight_target": "photos",
    "note": "Etsy takes up to 10 photo files (bytes, not URLs); an imported eBay listing's photos are fetched and re-uploaded"
  },
  {
    "key": "format",
    "label": "Selling format",
    "fill": "shared",
    "derive": "",
    "required_for_etsy": true,
    "preflight_target": "format",
    "note": "Etsy has no auctions — Buy It Now only"
  },
  {
    "key": "condition",
    "label": "Condition",
    "fill": "derived",
    "derive": "strip_html+condition",
    "required_for_etsy": false,
    "preflight_target": "",
    "note": "Etsy has no condition field; it is appended to the description"
  },
  {
    "key": "brand",
    "label": "Brand",
    "fill": "derived",
    "derive": "tags_from_brand_and_specifics",
    "required_for_etsy": false,
    "preflight_target": "",
    "note": "Becomes the first Etsy search tag"
  },
  {
    "key": "item_specifics",
    "label": "Item specifics",
    "fill": "derived",
    "derive": "tags_from_brand_and_specifics",
    "required_for_etsy": false,
    "preflight_target": "",
    "note": "Values become Etsy search tags: up to 13, 20 characters each, accents plain"
  },
  {
    "key": "materials",
    "label": "Materials",
    "fill": "derived",
    "derive": "materials_from_specifics",
    "required_for_etsy": false,
    "preflight_target": "",
    "note": "Etsy shows materials on the listing; read off the Material specific"
  },
  {
    "key": "package",
    "label": "Package weight and size",
    "fill": "shared",
    "derive": "",
    "required_for_etsy": false,
    "preflight_target": "",
    "note": "Feeds Etsy's calculated shipping when present"
  },
  {
    "key": "category",
    "label": "Category",
    "fill": "derived",
    "derive": "taxonomy_suggest",
    "required_for_etsy": true,
    "preflight_target": "etsy_taxonomy",
    "note": "Etsy's tree is its own: the eBay category path is matched to it first, the AI picks from a shortlist when it isn't, and the seller confirms"
  },
  {
    "key": "who_made",
    "label": "Who made it",
    "fill": "manual",
    "derive": "",
    "required_for_etsy": true,
    "preflight_target": "etsy_attribution",
    "note": "Etsy's policy question — handmade, vintage or supplies — answered once per crosspost batch, never defaulted"
  },
  {
    "key": "when_made",
    "label": "When it was made",
    "fill": "derived",
    "derive": "when_made_from_specifics",
    "required_for_etsy": true,
    "preflight_target": "etsy_attribution",
    "note": "Read off a decade or year specific, or a year in the title, when there is one; otherwise the batch default or the seller"
  },
  {
    "key": "is_supply",
    "label": "Craft supply",
    "fill": "manual",
    "derive": "",
    "required_for_etsy": false,
    "preflight_target": "etsy_attribution",
    "note": "Etsy's third allowed kind of item"
  },
  {
    "key": "shipping_profile",
    "label": "Shipping profile",
    "fill": "manual",
    "derive": "",
    "required_for_etsy": true,
    "preflight_target": "etsy_shipping_profile",
    "note": "Account default under Settings, per-listing override on the Etsy card"
  },
  {
    "key": "return_policy",
    "label": "Return policy",
    "fill": "manual",
    "derive": "",
    "required_for_etsy": true,
    "preflight_target": "etsy_return_policy",
    "note": "Required before an Etsy listing goes live; a draft may wait"
  },
  {
    "key": "readiness_state",
    "label": "Processing time",
    "fill": "manual",
    "derive": "",
    "required_for_etsy": true,
    "preflight_target": "etsy_readiness_state",
    "note": "Etsy's processing profile, required on every physical listing"
  },
  {
    "key": "variations",
    "label": "Variations",
    "fill": "absent",
    "derive": "",
    "required_for_etsy": false,
    "preflight_target": "variations",
    "note": "No variation model here; a listing with them is refused for Etsy"
  },
  {
    "key": "subtitle",
    "label": "Subtitle",
    "fill": "absent",
    "derive": "",
    "required_for_etsy": false,
    "preflight_target": "",
    "note": "An eBay paid upgrade; Etsy has nothing like it"
  },
  {
    "key": "store_category",
    "label": "Store category",
    "fill": "absent",
    "derive": "",
    "required_for_etsy": false,
    "preflight_target": "",
    "note": "The seller's own eBay Store shelf; Etsy shop sections are not mapped yet"
  },
  {
    "key": "promote",
    "label": "Promoted Listings",
    "fill": "absent",
    "derive": "",
    "required_for_etsy": false,
    "preflight_target": "",
    "note": "eBay only"
  },
  {
    "key": "sku",
    "label": "SKU",
    "fill": "absent",
    "derive": "",
    "required_for_etsy": false,
    "preflight_target": "",
    "note": "Etsy's SKU lives on the inventory product and is carried over on a revise, never written"
  }
];

export const fieldRule = (key) => FIELD_MAP.find((r) => r.key === key) || null;

// The rules Etsy requires, in map order — what the crosspost review has to
// see answered before a listing can go.
export const ETSY_REQUIRED = FIELD_MAP.filter((r) => r.required_for_etsy);
