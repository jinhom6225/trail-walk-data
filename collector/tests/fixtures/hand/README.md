Hand-made fixtures for error paths only (timeouts are simulated in code).
Every normal-path test uses the real responses in `../real/`.

- `api_result_code_22.json` — JSON body with a failing resultCode.
- `api_gateway_error.xml` — data.go.kr gateway error body (XML even with `_type=json`).
- `gpx_truncated.gpx` — GPX cut off in the middle of a track.
- `gpx_doctype.gpx` — GPX with a DOCTYPE/ENTITY (must be refused before parsing).
- `gpx_wpt_only.gpx` — waypoints but no track points.
