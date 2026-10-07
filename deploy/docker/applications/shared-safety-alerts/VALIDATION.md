<!--
SPDX-FileCopyrightText: 2026 the VSS application contributors
SPDX-License-Identifier: Apache-2.0
-->

# Shared-stack playback validation

Tested on 5 October 2026, 15:33–15:36 IST, on DGX Spark GPU 0. Both inputs
played simultaneously through the same Cosmos Reason 3 Nano BF16 service.
The VLM container ID and start time matched before and after the deployment
and test: the model was neither duplicated nor reloaded.

| Application | Single-pass duration | Positive windows | Negative windows | Median result delay after window end |
| --- | ---: | ---: | ---: | ---: |
| Person collapse | 111.64 s | 3 | 24 | 2.93 s |
| Woman hailing | 136.65 s | 15 | 18 | 2.97 s |

Positive windows are raw alert chunks, not separate physical incidents.
The shared API's default consolidation grouped each application's positive
windows into one returned event. The existing woman-hailing prompt and
inference settings were preserved; person collapse used its own prompt on its
own feed. Observed chunks were about four seconds long, including a shorter
final chunk at EOS.

Verification:

- Both publishers completed normally, with exit code 0 and no restart.
- Independent RTSP readers completed normally, with exit code 0.
- Neither MediaMTX path was ready after the publishers reached EOF.
- Both Alert Bridge caption tasks finished cleanly.
- The UI's verified-incident API returned all 18 positive windows with the
  correct sensor names and categories; Kafka's incident offsets grew by 18.
- Both camera-specific rules were saved and remained listed after EOS.
- VIOS retained recordings for both test sensors after EOS.
- All 19 resolved stack services were healthy or completed successfully.
- GPU utilization returned to 0% after the finite inference tasks ended.
- Shell syntax, Python compilation, Ruff lint, and Ruff formatting passed.

The live UI is `http://10.15.16.210:3000`. Select **Alerts → Manage Alerts →
Real-time Alerts** to see the rules, and **View Alerts** to see the detections.
The test feeds are offline after EOS. The stock rule API still labels the
saved configurations `active`; neither video nor inference is still running.

Machine-readable evidence is in the ignored
`_builds/shared-safety-alerts/playback-test/` directory: `results.json`,
`vlm-stats.json`, `ui-incidents-verified-true.json`,
`rtsp-paths-after-eos.json`, and publisher, reader, and service logs. Resolved
Compose files and credentials remain outside Git.

These two videos establish shared-service operation, detection, delivery,
recording, and finite RTSP playback. They do not establish general detection
accuracy, and rule admission started several seconds into each live feed.

## Alert retention correction

The initial deployment inherited the stock four-hour Elasticsearch retention
policy. At 17:15:27 IST on 5 October 2026, Elasticsearch deleted
`mdx-vlm-incidents-2026-10-05`. This made the UI's one-day query return no
results even though the original playback had generated alerts.

The bundle now sets only verified-incident retention to seven days. The
initializer was rebuilt and completed successfully; other policies retain
their four-hour setting. All 18 original records were restored from the saved
UI response using their original document IDs, timestamps, and contents.
Neither video was replayed. A one-day query with VLM Verified enabled returned
all 18 records with both All and Confirmed verdict filters. Separate sensor
queries returned 3 collapse and 15 woman-hailing records. The returned record
contents matched the saved originals exactly.

Recovery evidence is in
`_builds/shared-safety-alerts-retention/alert-recovery/`. The updated resolved
deployment is `_builds/shared-safety-alerts-retention/resolved.yml`; the
original playback evidence remains in its original build directory. For the
UI, keep **Query range: 1d**, **VLM Verified: on**, **Verdict: All**, and camera
and category filters cleared. Use **Refresh now** or reload the browser.
