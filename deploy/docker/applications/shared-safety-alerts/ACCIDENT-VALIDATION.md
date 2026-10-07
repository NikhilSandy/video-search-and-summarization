<!--
SPDX-FileCopyrightText: 2026 the VSS application contributors
SPDX-License-Identifier: Apache-2.0
-->

# Earlier accident prompt experiments

Historical evidence from the initial run. The current prompt includes aftermath
and is documented in [ACCIDENT-RERUN-20261006.md](ACCIDENT-RERUN-20261006.md).

The accident application uses the running shared safety stack. Only a camera and
its Alert Bridge rule were added. No Compose service, model, database, or UI was
duplicated or restarted. The deployed rule uses `traffic_accident` and sensor
`accident_detection_live`; its prompt and structured output settings are in
`accident-detection.json`.

## One moving-video playback

Input: `/home/awiros-tech/Projects/VSS_Apps/accident_videos/accident_detection.mp4`
(19.44 seconds, 1920×1080, 25 FPS). A prepared H.264 copy included a 12-second
first-frame hold for camera/rule admission, followed by the original footage
once at normal speed. Publisher exit was 0 after 31.475 seconds; an independent
RTSP reader exited at EOF, sampled 31 frames, and recorded 28 distinct hashes.
Monitoring admission took 6.962 seconds, within the hold.

The baseline prompt used 4-second windows, 2-second overlap, 4 FPS, 960×540 VLM
input, temperature 0, no reasoning, and the existing explicit JSON decision
parser. All 14 classifications were valid JSON. Chunks 4 and 5 detected the
visible accident; chunks 9 and 13 described additional crashes unsupported by
the reviewed footage. Four positive windows were indexed. These are overlapping
window detections, not four actual accidents. Descriptions of collision partners
were inconsistent and must not be treated as established facts.

The native VLM log reported the query `successful` and 100% complete. The audit
runner incorrectly waited for an empty terminal caption even though native EOS
ends with a normal final caption. It was interrupted after all 14 classifications
arrived. `play-single-once.py` now checks contiguous chunk indices and coverage
through EOF; the moving video was not rerun to test that bookkeeping correction.
The interrupted runner status is retained in the evidence.

## Prompt refinement

The first revision required an observed impact response or an uncontrolled crash,
rejected inferred hidden contact, and distinguished a new accident from aftermath.
It returned Yes on a 0–4-second accident clip and No on clips at 6–10, 10–14,
and 15.4–19.4 seconds. However, its subsequent live admission on a still image
produced false positive descriptions. Those results are retained too.

The final revision additionally requires visible change across chronological
frames and explicitly rejects frozen scenes. It contains no vehicle colors,
particular vehicle types, camera coordinates, scene layout, timestamps, or
identities from the input video. It covers traffic impacts, uncontrolled crashes,
overturns, and riders falling onto the road; routine traffic, near misses,
existing fallen people, and assistance alone are negative.

Final file checks through `vss vlm run`, on the same shared model at 4 FPS,
temperature 0, and no reasoning:

| Clip | Expected | Final decision |
| --- | --- | --- |
| Frozen first frame, 4 seconds | No | No |
| Original 0–4 seconds | Yes | Yes |
| Original 10–14 seconds | No | No |
| Original 15.4–19.4 seconds | No | No |

File checks use the CLI chat path, with system instructions prefixed to the prompt;
the live rule uses the native stream path with a system prompt and JSON schema.
These checks are not an equivalent second live replay and do not establish
accuracy on other videos. The event decision succeeded on these samples, but
some cue descriptions still overstate the identity of the collision partner.

The final configuration replaced this task's earlier accident rules. A finite
first-frame still feed admitted it without replaying the original moving footage.
The final live still-feed check returned No in all nine windows and native
inference completed successfully. Final rule ID:
`ca292bee-173e-43fa-8c0d-219d2f34ee12`.

The saved API `active` status describes the rule configuration; the test source
is offline after EOF. Existing unrelated rules and incidents are retained.

Evidence is in `_builds/accident-once-20261006/`: baseline and candidate prompts,
raw Kafka captions, native logs, publisher/reader logs, frame hashes, file-check
responses, API receipts, rule inventories, and `validation.json`.
