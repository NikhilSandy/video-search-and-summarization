<!--
SPDX-FileCopyrightText: 2026 the VSS application contributors
SPDX-License-Identifier: Apache-2.0
-->

# Camera Health live validation

## Full single-pass replay (6 October 2026)

The full 542.08-second supplied recording was replayed once at real-time rate
from 03:48:20 to 03:57:22 UTC (09:18–09:27 IST). Both the publisher and the
independent RTSP reader exited successfully, and the source stopped at EOF.
The run classified 135 segments: 69 healthy and 66 positive, with zero invalid
responses. All 66 positives matched exactly one Elasticsearch and UI record
per request/segment identity. Counts were 29 obstruction, 27 bright-light
interference, five view changes, and five low-illumination alerts. The earlier
three saved rule IDs were retained; the refreshed Camera Health rule ID is
`6f76acfd-3835-47d2-b5f5-42766871cb2b`.

For 134 full four-second windows, delivery after the window end was 2.08
seconds median, 2.31 seconds at the 95th percentile, and 2.65 seconds maximum.
The run uses the same one-call, scalar-class policy. Results remain in the UI;
this replay did not clear its alerts.

A reported UI playback problem exposed a separate VIOS preview failure:
recorded-image requests returned HTTP 500 after 20-second decoder timeouts.
Enabling `data.use_software_path` in the shared VIOS runtime configuration
and restarting stream processing restored snapshots. The exact UI request
for a 256 × 114 preview then returned HTTP 200 and a valid JPEG. A clip from
the same recorded alert window returned HTTP 200, valid H.264/960 × 540 MP4,
and decoded successfully. The UI already rewrites VIOS's internal clip URL
onto its configured public origin.

VIOS reconnected late and began this run's recording at 03:50:16 UTC,
approximately two minutes after publishing started. Initial alert windows
therefore have no corresponding recorded video; completing the playback
cannot supply that missing recording. Forty-three alert windows fit fully
within the reported recording ranges. The other 23 include the early gap
and a terminal partial window extending 39 ms past the recorder end. Preview
retrieval is independent of whether the publisher has reached EOF.

Evidence is in ignored `_builds/camera-health/replays/20261006T034819Z/`,
including `results.json`, `windows.json`, `indexed-incidents.json`,
`ui-incidents.json`, `recording-timeline.json`, `recording-coverage.json`,
`ui-preview.jpg`, `ui-clip.mp4`, and `vios-preview-fix/validation.json`.
The prior VIOS configuration is backed up in `vios-preview-fix/config-before.json`.

## Single primary class migration

On 5 October 2026 at 20:39–20:41 UTC, a short excerpt starting around 144
seconds in the supplied recording was published once through the shared
stack. The updated rule returned scalar JSON, for example
`{"class":"view_obstruction"}`, using one model call per window. The schema
and mapper reject lists, multiple labels, duplicate keys, and extra fields.

Sixteen valid segments produced 14 indexed positive records and two healthy
results. All four impairment classes occurred: three obstruction, seven
bright-light interference, two view-change, and two low-illumination segments.
Every positive request/segment identity matched exactly one indexed record;
healthy segments emitted none. This verifies the output contract and
integration, rather than independently annotated cause-selection accuracy.

For the 15 full four-second windows, delivery after the window end was
2.15 seconds median and 2.24 seconds at the 95th percentile/maximum. The
terminal 0.76-second partial window returned one valid bright-light class
32.30 seconds after its end, during EOF flushing; it is reported separately
from normal streaming windows. These measurements exclude UI polling.

All 76 previous Camera Health records and all 14 validation records were
backed up locally and deleted from Elasticsearch. A refreshed count and the
UI incidents API both returned zero Camera Health records. The 38 other
incident documents and the three earlier rule IDs were retained. The public
Elasticsearch ingress restricts administrative writes, so the scoped deletion
used Alert Bridge's existing configured Elasticsearch client locally; the
ingress ACL was preserved.

The old Camera Health rule was replaced with scalar-configured rule
`5acae67e-6507-41f0-a771-59498f3df20b`. Replacing the old registration was
necessary because RT-VLM retained its inactive decoder registration after
previous EOF. No shared model/service restart was required. The saved rule
remains active as a configuration; the finite video publisher is stopped.

Ten classification/protobuf regression tests passed, including request-schema
agreement and strict rejection of list and multiple-label responses. Lint,
compilation, Compose validation, and whitespace checks passed.

Upgrade evidence is in ignored `_builds/camera-health/single-class-migration/`:
`live-result.json`, `windows.json`, `live-incidents.json`, `latency.json`,
`rules-after.json`, `cleanup-validation.json`, and `ui-after-deletion.json`.
The original records, rule, runtime snapshots, and window audit are backed up
there. The worker's current `event-evidence/windows.jsonl` uses a scalar
`class` field, with `null` for invalid responses.

## Historical multi-class run (superseded)

The original results below used the former list response. The current
application returns one primary class per segment; these measurements must
not be interpreted as validation of the updated classifier.

The 542.08-second `camera-health-29sep26.mp4` recording completed a single
real-time playback on 5 October 2026, from 17:04:43 to 17:14:06 UTC.
Both the publisher and an independent RTSP reader exited successfully, and
the playback path was offline at EOF.

One Camera Health rule uses the existing Cosmos Reason 3 Nano model, one
classification request per window, and the existing Kafka/Logstash/Elasticsearch
alert path. No additional model instance was deployed. The CPU mapper consumed
about 31 MiB during playback.

The run produced 134 valid classified windows: 70 healthy and 64 positive.
Twelve positive windows contained multiple classes. Every emitted
request/window/class identity reconciled with an indexed incident, and the
Alerts UI's `vlmVerified=true` API returned the same 76 records.

| Class | Raw incident records |
| --- | ---: |
| `view_obstruction` | 41 |
| `bright_light_interference` | 22 |
| `camera_view_changed` | 6 |
| `illumination_too_low` | 7 |

These are records from overlapping condition detections, not consolidated
incident counts or independently annotated accuracy scores.

For valid windows, delay from the window's end timestamp to delivery at the
Kafka mapper was 1.93 seconds median, 2.59 seconds at the 95th percentile, and
2.75 seconds maximum. This excludes the four-second observation window and
UI indexing/polling delay. The deployed live path emitted windows every four
seconds despite the requested two-second overlap. Other simultaneous camera
loads were not benchmarked.

A late empty caption arrived for the final 2.52-second partial window after
EOF. It was recorded as invalid and produced no incident; it was not counted
as healthy or included in classification latency. The playback helper now
waits 35 seconds after EOF so this terminal response is included in future
test evidence.

Recovery required an opt-in FFprobe metadata backend, its missing audio/device
dependencies, and software H.264/H.265 decoding on this SBSA host. Hardware
metadata discovery and live decoding both blocked. A separate check decoded
32 live frames in software and converted them into GPU memory successfully.
The model still runs on the original shared GPU. The missing `mdx-vlm-errors`
topic was also provisioned with the shared Kafka defaults after shutdown
exposed metadata waits.

Eight classification/protobuf tests, five metadata-probe tests, and six
codec-installer tests passed. Application lint, Python compilation, Compose
validation, and whitespace checks passed. The three earlier saved alert rules
were retained, and exactly one Camera Health rule remains for
`camera_health_29sep26`.

Evidence is saved under ignored `_builds/camera-health/`:

- `playback-test/validation.json`: counts, latency, invalid terminal response,
  and retained rule IDs.
- `playback-test/results.json`: rule admission, publisher/reader exit status,
  EOF path state, and the initial incident query.
- `playback-test/ui-incidents-verified-true.json`: the UI API's incident results.
- `playback-test/rules-after-eos.json`: saved rules after playback.
- `single-class-migration/windows-before.jsonl`: archived original
  classifications and Kafka offsets.
- `manifest.json`: source, pinned image, and runtime snapshot hashes.

The video source is offline after the test. The saved rule's `active` status
does not indicate that a finite recording is still playing. This run verifies
integration on the supplied recording; deployment to other scenes still
requires detection-accuracy evaluation and camera-specific calibration.
