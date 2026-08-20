# Thryft Shop brand illustration assets

Transparent mascot illustrations derived from the final Thryft Shop logo. All five assets use the same navy, cream, coral, teal, yellow, and powder-blue visual system.

## Files and placement

| Asset | Intended placement | Suggested alt text |
| --- | --- | --- |
| `thryft-mascot-welcome` | Home greeting panel; replaces the robot | `Thryft Shop mascot waving` |
| `thryft-mascot-photo-upload` | Sell photo drop zone; replaces the camera placeholder | `Thryft Shop mascot holding a camera` |
| `thryft-mascot-listings` | Empty or logged-out listings state; replaces the tag placeholder | `Thryft Shop mascot holding a listings checklist` |
| `thryft-mascot-shop-mode` | Shop Mode empty state; replaces the storefront placeholder | `Thryft Shop mascot inspecting a thrift find` |
| `thryft-mascot-account` | Settings/login state; replaces the robot | `Thryft Shop mascot holding an account key` |

Each illustration is provided as:

- PNG: 1200×1200, full-resolution RGBA with transparent background.
- WebP: 640×640, optimized RGBA for the live app.

## Implementation notes

- Use the WebP files in production unless a downstream tool requires PNG.
- Display at about 120–180 px in the current desktop UI.
- Preserve aspect ratio with `object-fit: contain`.
- The transparent square canvas allows the same files to work in light and dark modes.
- Do not add a circular crop or colored tile behind the art; the coral offset outline already provides separation.

Example:

```jsx
<img
  src="/brand/thryft-mascot-photo-upload.webp"
  alt="Thryft Shop mascot holding a camera"
  width={160}
  height={160}
  className="object-contain"
/>
```

