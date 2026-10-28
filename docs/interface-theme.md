# Interface materials and pocket mark

The interface keeps its page order, charts and controls. Planning is the sixth
mobile bottom tab; Overview, Banking, Brokerage, Spending and Activity remain.
The desktop navigation is unchanged. Content reserves space for the bottom
bar, demonstration control and device safe area.

The material treatment uses ivory, sand and stone light surfaces, warm charcoal
dark surfaces, charcoal type and restrained brass brand actions. Green and red
primarily convey positive and negative financial meaning. Muted slate, clay,
plum, sage and brass distinguish chart categories. Soft CSS depth and fine
highlights retain the glass effect without a global green tint. The original
sky image is bundled locally; no web font, external asset request or new
dependency is required.

`mypocket/static/icon.svg` is an original pocket outline, reused in the desktop
and mobile header, unlock screen and favicon. The mark is decorative beside the
visible MyPocket wordmark; the standalone SVG has an accessible title. It has no
scripts, foreign objects, remote references or embedded raster data.

Theme variables and shared surfaces live in `assets/tailwind.css`. Rebuild the
checked-in `mypocket/static/styles.css` with the existing Tailwind CLI after
editing them. The `mp-card` class marks existing major panels. Translucent
holding rows have no backdrop blur; blur is limited to major panels, hero/summary
tiles and navigation. Inputs, alerts and chart text retain readable surfaces.

Opaque surfaces are the default for browsers without backdrop-filter support.
Reduced transparency or increased contrast preferences remove background depth
and blur. Reduced motion suppresses CSS motion and disables Chart.js animation
on page load. Authentication, demo isolation and financial calculations are
unchanged apart from the explicit incomplete-coverage presentation below.

Validation uses the source-only `tests.theme_preview:app` with fictional,
in-memory fixtures. Its unlock page is a visual fixture and accepts no
credentials. Never launch it in the live application checkout or use a real
browser profile. Test light/dark themes at phone and desktop widths, chart
hover/tap/keyboard behavior, planner contribution controls, menu navigation,
opaque/motion fallbacks, text/button contrast and chart-color contrast. No real
account data is needed for design review or screenshots.

Chart separators use the solid panel token in both themes, including after
switching themes. Test palette contrast against the actual dataset border,
not just the background behind the chart. Allocation retains its ticker-only
legend and on-demand percentages. Keyboard users can select any ticker with
arrow keys and hide or restore it with Enter or Space, even when all slices
are hidden. A missing account snapshot suppresses affected currency totals
and allocation with a concise coverage message; it never implies zero value.

The design review applies preservation and accessibility guidance from
`design-taste-frontend`. Its marketing layouts, imagery and animation defaults
do not apply to this financial dashboard. Existing page hierarchy, information
density, compact labels and quiet motion are deliberate; do not introduce
decorative content or restructure the interface to fit a marketing template.

## Sky background

The approved background uses an original AI-generated photorealistic sky served
from `mypocket/static/images/sky-daylight.jpg`.
It is not an actual camera photograph. No external image URL, new dependency or runtime
AI call is involved. The image contains only natural-looking clouds and sky, in soft
blue, warm-white and restrained peach tones. It contains no personal or app data.
The exact generation prompt is retained in `docs/sky-background-prompt.txt`.

A 12% saturation-only background blend gently reduces vibrancy while retaining
the original image and existing brightness overlays. It applies only to background
layers, not text, charts, or UI colors. Unsupported browsers retain the original
approved sky treatment. The implementation uses native
[background-blend-mode](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/background-blend-mode).

The background is the only visual change from the approved daylight-interior version.
Individual glass cards, navigation, typography, six mobile tabs, the centered allocation
and all interactions stay unchanged. There is no enclosing outer glass shell. Dark
mode dims the same local sky image. Reduced-transparency and increased-contrast
preferences remove the image and glass effects in favor of the existing opaque panels.

Validation uses fictional desktop and mobile light/dark views, the actual composited
text contrast, mobile image crop and loading, and opaque fallback. No real financial
records, screenshots or account responses are used.
