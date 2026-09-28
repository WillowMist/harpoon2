# Harpoon logo — usage guide

This is a one-page reference. If you only read one section, read **Quick start**.

## Quick start

```html
<!-- Navbar / app icon (any context, recommended): -->
<img src="{% static 'images/logo_64.png' %}" width="40" height="40" alt="Harpoon">

<!-- Where to put each variant: -->
<!-- 16/32 px   → harpoon-small.svg    (H only, no arrowheads — pure letterform) -->
<!-- 48 px +    → harpoon-master.svg   (full mark with harpoon-tipped verticals) -->
<!-- Any size, no disc  → harpoon-mark.svg / harpoon-mark-small.svg -->
<!-- For dark backgrounds → use white version via CSS filter or swap fill -->
```

The masters are SVG. PNG/ICO rasters at every standard size ship alongside.

## Variants

| File | When to use |
|---|---|
| `harpoon-master.svg` | App icon, hero image, anywhere the full mark with disc fits. |
| `harpoon-small.svg` | 16-32 px contexts (favicons, browser tabs). Drops the harpoon arrowheads; the letterform alone is more legible at that size. |
| `harpoon-mark.svg` | Full mark without the disc container, for placement on an existing colored surface. |
| `harpoon-mark-small.svg` | H only, no disc. |
| `favicon-16x16.png`, `favicon-32x32.png` | Small-cut raster favicons. |
| `favicon.ico` | Multi-size (16/32/48) ICO for legacy browsers. |
| `logo_48.png`, `logo_64.png` | Navbar/app rasters — full mark. |
| `apple-touch-icon.png` (180) | iOS home-screen icon. |
| `android-chrome-192x192.png` (192) | Android home-screen icon. |
| `android-chrome-512x512.png` (512) | Splash / Play Store. |
| `mstile-150x150.png` (270 actual) | Windows tile. |

## Color palette

| Token | Hex | Use |
|---|---|---|
| `harpoon-ink` | `#1E1B4B` | Disc background and primary brand color. |
| `harpoon-white` | `#FFFFFF` | Mark on `harpoon-ink`. |

**Why indigo, not navy?** The previous mark was a near-black navy that read as a generic dark blue. `#1E1B4B` is the same family but with a deliberate violet undertone — the brand chooses its color rather than inheriting a default. If you want to revert to the prior navy, swap `#1E1B4B` for `#001A3D` in `harpoon-master.svg` and re-render all rasters.

**Contrast:** `#1E1B4B` against white is 14.6:1 (WCAG AAA). White against `#1E1B4B` is the same. Never use `#1E1B4B` text on `#FFFFFF` smaller than 14 px without testing.

**No other colors.** No gradients, no accent fills, no second brand color until Phase 2. If you need a hover/active state, use opacity on the disc (e.g. `fill="#1E1B4B" fill-opacity="0.85"`).

## Sizing rules

- **Minimum size for the full mark (with arrowheads):** 40 px. Below this, use the small cut.
- **Minimum size for the small cut:** 12 px.
- **Always scale in proportion.** Never stretch, never crop the disc.
- The disc has a 4% inner safe area — keep critical content inside the disc on r=120px circle.

## What not to do

1. **Don't add drop shadows, bevels, or 3D effects** to the disc.
2. **Don't recolor the mark** to anything other than `harpoon-white`. If you need a dark mark on a light bg, use `harpoon-mark.svg` and override the fill in CSS, or use the original black audit (`<path fill="#000000">`) at small sizes where contrast is needed.
2a. **Don't place the indigo mark on a same-tone background.** `harpoon-mark.svg` (#1E1B4B) is invisible against `#1E1B4B` and barely visible against deep navys (`#0F4C81`, `#001A3D`). On those surfaces, use the master (with disc) or recolor the mark via CSS to a contrast color (e.g. `#FFFFFF` or `#F5C518` for warm UI).
3. **Don't outline or stroke the mark.** The shapes are filled. Stroking creates rendering discrepancies at small sizes.
4. **Don't tilt or rotate the mark.**
5. **Don't put the mark inside another frame, badge, or container.**
6. **Don't change the letterforms to a custom font.** The H strokes are drawn as paths for a reason — they are an explicit choice and render identically on every device.
7. **Don't replace the disc with a square tile.** Rounded corners are part of the mark; ship the disc.

## How the file is built (so a future edit is safe)

- **Disc:** `<circle cx="128" cy="128" r="120" />` — radius 120 in a 256 viewBox = 4% safe area on each side.
- **H verticals:** two `<rect>`s at the same width so they read as equal weight.
- **Crossbar:** one `<rect>` at the visual middle of the shaft (NOT at the geometric middle of the full silhouette including arrowheads).
- **Harpoon arrowheads:** two `<polygon>` triangles with shoulders at the bottom of the shaft, points ~44 units below.
- **Small cut:** same construction minus the polygons. The verticals simply extend to where the arrowhead points would have been.

Geometry is intentionally chunky — every shape survives down to 16 px. Do not "refine" it by adding curves, gradients, or effects.

## If you need to rebuild the rasters

```bash
# from /home/willow/development/harpoon2/static/images/
for size in 16 32; do
  python3 -c "import cairosvg; cairosvg.svg2png(url='harpoon-small.svg', write_to='favicon-${size}x${size}.png', output_width=${size}, output_height=${size})"
done

python3 -c "
import cairosvg
for s, name in [(48,'logo_48.png'),(64,'logo_64.png'),(180,'apple-touch-icon.png'),(192,'android-chrome-192x192.png'),(512,'android-chrome-512x512.png'),(270,'mstile-150x150.png')]:
    cairosvg.svg2png(url='harpoon-master.svg', write_to=name, output_width=s, output_height=s)
"

python3 ~/.agents/skills/logo-design/scripts/render_png.py harpoon-master.svg --ico favicon.ico --ico-sizes 16 32 48
```

(Or just re-run any of the build scripts if you tweak the master.)

## Naming

- **URL name:** `archive_item` (underscore convention used by the Django app for routes)
- **File path:** `static/images/logo_64.png` and friends (slash convention)
- **Mark:** "Harpoon" (no "2"; the brief was to drop the version number from the brand surface)

— end —
