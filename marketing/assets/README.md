# Marketing assets — drop zone

Source masters live here at **full resolution**. The build generates optimized
`.webp` derivatives into `marketing/public/` — never hand-optimize a master.

This mirrors the convention already used by the app
(`frontend/assets/thryft-shop-brand-assets/` masters → `frontend/public/brand/`
derivatives), so there is one way to add art to this project, not two.

| Folder | What goes in it |
|---|---|
| `logo/` | Full lockups, wordmark, monochrome, SVG if you have it |
| `mascot/` | Price-tag character poses. Transparent PNG preferred, square canvas |
| `screenshots/` | Web and phone captures, raw and uncropped — the build frames them |
| `video/` | Demo captures (MP4/MOV). Poster frames are generated |
| `photography/` | Lifestyle and product shots |
| `badges/` | Official App Store / Play badges, once the listings exist |

## Naming

`kebab-case`, describing the content rather than the placement, so an asset can
be reused without its filename lying:

```
mascot/tag-photographing-rack.png       not  hero-image.png
mascot/tag-with-phone-wand.png
mascot/tag-pushing-cart.png
screenshots/web-dashboard-light.png
screenshots/ios-new-listing.png
```

## Notes on what's already been sent

Five mascot illustrations came through in chat and still need to land here as
files. Suggested placements:

- Wide cream banner (tag photographing a jacket on a rack, pipeline flowing to
  a shipped box) → `mascot/` — this is the **home hero**; it already tells the
  whole snap → identify → publish story in one image
- Tag with phone + magic wand → `mascot/` — the AI drafting section
- Tag photographing jacket + vase → `mascot/` — "works on anything you sell"
- Tag pushing a cart of listing cards → `mascot/` — the bulk / multi-marketplace
  section (two versions arrived; keep the **transparent** one, drop the white
  matte — a transparent canvas works on both light and dark backgrounds)

Transparent PNG at 1200px square or larger, matching the existing mascot
masters. If you have the originals at higher resolution, send those instead.
