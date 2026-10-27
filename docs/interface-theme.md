# Interface materials and pocket mark

The interface keeps its existing navigation, page order, charts and controls.
The material treatment uses warm neutral light surfaces and deep teal dark
surfaces, subtle CSS background depth, fine highlights and restrained chart
colors. No photographic background, web font, external asset request or new
dependency is required.

`mypocket/static/icon.svg` is an original pocket outline, reused in the desktop
and mobile header, unlock screen and favicon. The mark is decorative beside the
visible MyPocket wordmark; the standalone SVG has an accessible title. It has no
scripts, foreign objects, remote references or embedded raster data.

Theme variables and shared surfaces live in `assets/tailwind.css`. Rebuild the
checked-in `mypocket/static/styles.css` with the existing Tailwind CLI after
editing them. The `mp-card` class marks existing major panels. Translucent
holding rows and stat tiles have no backdrop blur; blur is limited to major
panels and navigation. Inputs, alerts and chart text retain readable surfaces.

Opaque surfaces are the default for browsers without backdrop-filter support.
Reduced transparency or increased contrast preferences remove background depth
and blur. Reduced motion suppresses CSS motion and disables Chart.js animation
on page load. Existing navigation, authentication, demo isolation and financial
calculations are unchanged.

Validation uses the source-only `tests.theme_preview:app` with fictional,
in-memory fixtures. Its unlock page is a visual fixture and accepts no
credentials. Never launch it in the live application checkout or use a real
browser profile. Test light/dark themes at phone and desktop widths, chart
hover/tap/keyboard behavior, planner contribution controls, menu navigation,
opaque/motion fallbacks, text/button contrast and chart-color contrast. No real
account data is needed for design review or screenshots.
