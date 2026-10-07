<!--
SPDX-FileCopyrightText: 2026 the VSS application contributors
SPDX-License-Identifier: Apache-2.0
-->

# Woman hailing, person collapse, and accidents on shared services

This bundle updates the existing woman-hailing deployment in place. One Cosmos
Reason 3 Nano BF16 model, VIOS recording, Alert Bridge, Kafka, Elasticsearch,
and UI serve independent, camera-specific applications. The existing
Compose project name and data directory are retained so that no model or
backend is duplicated.

Woman hailing imports the prompt and inference settings from
`../woman-hailing/realtime-config.yml`. Person collapse uses the concise
105-word prompt in `person-collapse.json`: chronological visual cues followed
by Yes only for a new downward fall into a full-body lying posture. Sitting,
backside-only landings, supported reclining, controlled lowering, already lying,
and getting up alone are No. The explicit-decision parser reads only the JSON
`decision` field and retains the cues; it adds no second model call or motion
rules in code. The current collapse configuration uses 8-second windows with
2-second overlap (25%), a 6-second step, and 4 sampled frames per second.

The prompt returned No on both user-reported negative recorded windows, chunks
144 and 260, at 4 FPS. With user authorization, the parser and live-overlap fix
were then loaded into the existing RT-VLM service, preserving its model image
and existing camera-health mounts. Three earlier person-collapse cameras, two
collapse rules, and 23 corresponding alerts were removed before the new run.

The earlier `person_collapsing.mp4` run completed one full normal-speed playback as
`person_collapsing_live`, using 4-second windows, 2-second overlap, and 4 FPS.
There were 54 valid JSON classifications and 9 indexed Yes alerts. These are
positive windows, not a count of distinct falls: chunks 35, 36, and 37 each
created an alert for overlapping footage of the user-reported single fall.
The publisher currently has no fall-event grouping. Live frame
hashes verified motion and the source reached EOF. A brief setup feed was aborted
before monitoring because VIOS returned a proxy URL; that registration was removed
before the successful full pass. Monitoring began 7 seconds after the full feed
started. Model processing lag grew to about 96 seconds, so the final result count
includes inference drained after EOF. Model cue accuracy has not been independently
verified. Existing unrelated rules were retained.

On the user's next request, the 9 collapse alerts, collapse rule, and source
with its recordings were removed. The same original video completed a fresh
single normal-speed playback with the current 8-second windows and 25% overlap.
Native outputs verified 32 frames per full window and a 6-second step. There
were 18 valid classifications and 6 indexed Yes alerts; positive chunks 11 and
12 still overlap and describe a backward fall. This adjustment reduced the
raw alert count but did not add event grouping or independently validate the
model's cues. Monitoring registration took 7.4 seconds; processing latency was
19.6 seconds at the median and 21.3 seconds at the maximum. Evidence is stored
under `_builds/person-collapse-8s-once-20261006T061834Z/validation.json`.

The run's ignored build directory contains deployment snapshots, cleanup receipts,
all native caption responses, indexed incidents, and validation evidence. Editing
these application files alone does not update an already saved Alert Bridge rule.
Use the generic `decision-parser.compose.yml` overlay, with
`VSS_COLLAPSE_SOURCE_ROOT` set to the absolute repository root, when deploying the
parser elsewhere. A model rebuild can include the same source change.

Global always-on camera assignment is disabled in this bundle. Rules are saved
through Alert Bridge's real-time API, so they appear in **Alerts → Manage
Alerts → Real-time Alerts**. Each rule targets only its assigned sensor. The
existing UI can show both categories, filter incidents, duplicate rules, and
select cameras; this bundle does not add a new application-picker component.

Verified alert incident indices are retained for seven days through
`ELASTICSEARCH_VLM_INCIDENTS_ILM_MIN_AGE=7d` in `bundle.env`. Other perception
indices retain their existing global setting. Elasticsearch measures the
retention period from index creation. The UI's query range changes the search
window; it does not extend storage retention. The stock four-hour policy can
therefore leave a one-day query empty even after successful detections.

## Resolve and update the existing stack

From the repository root, use the existing protected NGC credential file:

```bash
set -a
. /home/awiros-tech/.config/vss/ngc.env
set +a
export BASE_BUILD_DIR="$PWD/_builds/woman-hailing"
export BUILD_DIR="$PWD/_builds/shared-safety-alerts"
bash deploy/docker/applications/shared-safety-alerts/resolve.sh
```

The resolver writes the standard `override.env`, `compose.yml`, and
`resolved.yml` artifacts in the ignored build directory. It validates the
pinned VSS image version `develop-4c432f24cf39` (`-sbsa` for RT-VLM), preserves
the existing VLM definition, and replaces the camera webhooks with a disabled
projection. It refuses to overwrite an already resolved build; use a new
`BUILD_DIR` for another resolution. The base build must remain available,
because its working RTSP transport and host-mapping patches are reused.

Stop the old looping playback service and update only the changed services:

```bash
docker compose -f "$BASE_BUILD_DIR/resolved.yml" stop nvstreamer-alerts
docker compose -f "$BUILD_DIR/resolved.yml" build elasticsearch-init-container
docker compose -f "$BUILD_DIR/resolved.yml" up -d --no-deps elasticsearch-init-container
docker compose -f "$BUILD_DIR/resolved.yml" wait elasticsearch-init-container
docker compose -f "$BUILD_DIR/resolved.yml" pull --ignore-buildable \
  alert-bridge sensor-ms streamprocessing-ms vss-ui
docker compose -f "$BUILD_DIR/resolved.yml" up -d --no-deps \
  alert-bridge sensor-ms streamprocessing-ms vss-ui
docker compose -f "$BUILD_DIR/resolved.yml" restart vst-ingress
uv run --project libs/vss vss configure --base-url http://10.15.16.210:7777
uv run --project libs/vss vss configure check
```

Restarting VIOS ingress refreshes its cached addresses after recreating the
camera services. Check the Alert Bridge health endpoint, VIOS version endpoint,
and UI response before starting the test. The loaded VLM and database services
do not need to restart. Do not run a second Compose project or remove volumes.

## Prepare and play each video once

The single-pass playback helper uses MediaMTX 1.21.1 and host FFmpeg. It pins
and checks the Linux ARM64 server archive, and normalizes both videos to
25 FPS H.264 with TCP-friendly, single-slice encoding. Originals are untouched.

```bash
python3 deploy/docker/applications/shared-safety-alerts/prepare-playback.py \
  --build-dir "$BUILD_DIR" \
  --output-dir /home/awiros-tech/Projects/VSS_Apps/vss-data/shared-safety-alerts/playback \
  --person-video /home/awiros-tech/Projects/VSS_Apps/person_collapsing_videos/person_collapsing.mp4 \
  --woman-video /home/awiros-tech/Projects/VSS_Apps/videos/woman_test_office.mp4

python3 deploy/docker/applications/shared-safety-alerts/play-once.py \
  --build-dir "$BUILD_DIR" --host 10.15.16.210 \
  --person-video /home/awiros-tech/Projects/VSS_Apps/vss-data/shared-safety-alerts/playback/person_collapsing.mp4 \
  --woman-video /home/awiros-tech/Projects/VSS_Apps/vss-data/shared-safety-alerts/playback/woman_hailing.mp4
```

Preparation requires FFmpeg, Python, and PyYAML. Playback also uses the
project-local VSS CLI through `uv`. RTSP port 8554 and the loopback-only
MediaMTX API port 9997 must be free.

Each FFmpeg publisher reads its video once at normal speed and exits at EOF.
There is no `-stream_loop`, on-demand publishing hook, or publisher restart.
Independent RTSP readers prove that clients receive EOS. MediaMTX is stopped
after the test. VIOS records the same sources, while Alert Bridge sends the
direct source URL to RT-VLM, so a reconnecting VIOS proxy cannot mask EOS.
The test waits briefly for queued model work and indexing to finish, then saves
the results under `playback-test/` in the build directory.

| Application | Sensor | RTSP source during playback |
| --- | --- | --- |
| Person collapse | `bundle_person_collapsing` | `rtsp://10.15.16.210:8554/person_collapsing` |
| Woman hailing | `bundle_woman_hailing` | `rtsp://10.15.16.210:8554/woman_hailing` |

The helper refuses to reuse an existing `playback-test` evidence directory; it
never reruns a file automatically. The publishers start before camera
registration and rule admission, so inference begins several seconds into the
videos. This is a simultaneous live-pipeline smoke test, not a measurement of
accuracy over every frame. Saved rules remain in the UI after EOS. The stock
rule API labels a saved configuration `active` even after its finite caption
task completes; that label does not mean the test video is still playing.

## Traffic accident application

The current accident rule uses **8-second windows with 2-second overlap (25%)**,
so each next window starts 6 seconds later. These values are stored per rule,
but RT-VLM shares a decoder per camera: concurrently active rules on the same
camera must agree on window duration, overlap, frame sampling, input size, and
audio settings. Different cameras can use different settings. Prompts and
categories remain rule-specific. Longer windows reduce repeated classifications;
sustained aftermath can still produce more than one positive window.


`accident-detection.json` adds a third independent application to the same running
stack. Its camera-specific `traffic_accident` rule detects traffic accidents and
visible aftermath, including collisions, rollovers, and rider crashes. No additional
Compose deployment or model is required. The current short prompt accepts visible accident aftermath and rejects normal
traffic, near misses, and unclear evidence. See
[ACCIDENT-VALIDATION.md](ACCIDENT-VALIDATION.md) for the earlier prompt experiments and
[ACCIDENT-RERUN-20261006.md](ACCIDENT-RERUN-20261006.md) for the current short-prompt
run and cleanup receipts.

The latest single replay with 8-second windows and 25% overlap produced five
valid classifications and two indexed positive windows, after deleting the nine
previous accident alerts. Both fresh replay clips decoded correctly. The first
thumbnail is black because that window begins in the setup lead-in; the second
shows the road scene. Model collision-partner descriptions remain inconsistent,
and overlapping positives are still stored separately. Evidence is in
`_builds/accident-eight-second-once-20261006T064003Z/validation.json`.

To test another video once, use a fresh evidence directory and camera name:

```bash
python3 deploy/docker/applications/shared-safety-alerts/run-accident-once.py \
  --video /absolute/path/to/video.mp4 \
  --run-dir "$PWD/_builds/accident-new-test" \
  --host 10.15.16.210 \
  --sensor-name accident_new_test \
  --mediamtx "$PWD/_builds/shared-safety-alerts/mediamtx"
```

The helper uses `vss configure show` to target the existing deployment, prepares
an H.264 copy with a 12-second blank lead-in, publishes the moving footage
once without a video loop or restart, registers one VIOS camera, and saves its
rule through Alert Bridge. Ports 8554 and loopback 9998 must be free. It captures
both positive and negative Kafka responses, checks independent reader EOF and
motion, drains inference, and saves the results. A run directory cannot be
reused; a camera with an existing saved rule is rejected by the wrapper.

The original input is untouched. The final rule appears under **Alerts → Manage
Alerts → Real-time Alerts**, with the `traffic_accident` category available for
incident filtering. The camera and saved rule remain after the finite publisher
stops. Use `--reuse-sensor` to reuse a named VIOS camera after its earlier rule
has been explicitly replaced; the helper never deletes an existing rule itself. A real camera must supply an ongoing RTSP feed for continued monitoring.
