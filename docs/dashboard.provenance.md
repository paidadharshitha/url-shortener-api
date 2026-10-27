# Dashboard screenshot provenance

Asset: `docs/dashboard.jpg`.

The image is a native browser screenshot of the implemented Shortlink dashboard, captured on 2026-10-27 from the separate QA instance at `http://localhost:8002/`. It is not a generated image or a visual mockup. The capture record supplied by the build agent specifies no image editing; the shipping file preserves the native capture bytes.

- Source capture: `.impeccable/review/v2/desktop.png`.
- Native format and dimensions: JPEG, 1440 × 1295 pixels. The source capture has a `.png` filename, but its bytes are JPEG. The shipping copy was renamed from `dashboard.png` to `dashboard.jpg` to match that encoding; no conversion occurred.
- Account shown: the local `qa-workspace` test account.
- Data: three deliberately synthetic, visibly labelled records — Demo · Support, Demo · Documentation, and Demo · Product launch — using example.com destinations.
- Activity: five actual QA redirect requests, recorded by the application, with per-link totals of 1, 1, and 3. These are test clicks, not customer activity or illustrative chart values. The screenshot selects the 90-day UTC range, 2026-07-30 through 2026-10-27, and shows the resulting API totals.
- Production implication: none. The image documents a local QA workspace; it does not imply a public hosted deployment or depict the user's existing stored records.

The documentation check independently opened the capture, verified the displayed labels and counts, inspected the API-backed chart path, and confirmed the shipping file is byte-identical to the source capture. It did not repeat the redirect requests.

SHA-256 for both files: `f60b196498124ea4f569e9cf176fddf865a7ef41c4107f00b60724ae8db27d8c`.
