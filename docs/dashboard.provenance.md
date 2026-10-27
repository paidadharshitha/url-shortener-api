# Dashboard screenshot provenance

Asset: `docs/dashboard.jpg`.

Native browser screenshot of the implemented Shortlink dashboard, captured on 2026-10-27 from the separate QA instance at `http://127.0.0.1:8002/`. It shows the created short link and automatic QR preview in the `qa-workspace` account. It is not a generated image or a visual mockup. The build agent reports no image editing; the shipping file preserves the source capture bytes.

- Source capture: `.impeccable/review/qr-flow/desktop.jpg`.
- Native encoding and dimensions: JPEG, 1440 × 1543 pixels. No conversion or pixel edits occurred.
- Data: four visibly Demo-labelled QA records — QR sharing, Support, Documentation, and Product launch. QR sharing points to `https://www.wikipedia.org/?utm_source=qr-demo&utm_campaign=sharing`; the other three use example.com destinations.
- Activity: six actual QA redirects recorded by the application, with per-link totals of 1, 1, 1, and 3 in that order. Creating and downloading the new QR caused no clicks. The build agent verified that the new result's Open link action redirected in the browser and added the QR sharing record's one click.
- Selected period: Last 7 days, 2026-10-21 through 2026-10-27, with explicit UTC boundaries. The screenshot shows six clicks in the selected period and six clicks today.
- Visible state: the created result includes Copy link, Open link, the QR preview, Download QR (SVG), and the local-address sharing explanation. More tools is collapsed.
- Production implication: none. These are test records and actual test clicks in an isolated local QA workspace, not customer activity or the user's existing stored records. The image does not imply a public hosted deployment.

The documentation check independently opened the shipping image, verified its visible labels, counts, period, and result state, inspected the corresponding source paths, and confirmed matching encoding, dimensions, and hashes. It did not repeat the browser interactions or redirect requests; those interaction results come from the build agent.

SHA-256 for source and shipping files: `7c7ff9c7e68a1d0580c91d99450d5d4ff126ddfa20268b4aed8f9950071c03f7`.
