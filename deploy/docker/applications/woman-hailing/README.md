<!--
SPDX-FileCopyrightText: 2026 the VSS application contributors
SPDX-License-Identifier: Apache-2.0
-->

# Woman hailing alert application

This application specializes the VSS alerts developer profile for one DGX
Spark. Cosmos Reason 3 evaluates four-second video windows and emits an alert
when the same person appears to be a woman and visibly waves both hands above
her head, crossing and separating them. The result is an appearance-based
screening signal for human review; it does not establish gender identity,
intent, danger, or distress.

The configuration performs one VLM call per window. It also retains VIOS
continuous recording, configures reliable RTSP-over-TCP relay, and enables
looped MP4 playback through NVStreamer. Source videos, recordings, model
weights, resolved Compose files, and credentials remain outside Git.

## Resolve the deployment

Start from a shell containing the NGC credentials. Keep the credentials in a
mode `0600` file outside the repository and source it rather than writing a key
in a command, tracked file, or shell history.

```bash
set -a
. /path/to/protected/ngc.env
set +a
export HOST_IP=192.0.2.10
export VSS_DATA_DIR=/absolute/path/to/vss-data/woman-hailing
export NVSTREAMER_ALERTS_VIDEO_DIR=/absolute/path/to/playback-videos
deploy/docker/applications/woman-hailing/resolve.sh
```

`resolve.sh` creates a new, ignored build directory at
`_builds/woman-hailing-example`, generates the two runtime VIOS configuration
files, resolves the complete Compose model, and validates its bind mounts,
credentials, and image tag. It deliberately stops after resolution and does
not change running services. Set `BUILD_DIR` to another new directory under
`_builds/` when keeping multiple builds.

The application is pinned to the VSS checkout used during development:
`develop-4c432f24cf39` with the `-sbsa` image suffix. Update and validate that
pin deliberately when moving the application to another VSS release.

## Prepare playback media

NVStreamer loops every compatible MP4 in
`NVSTREAMER_ALERTS_VIDEO_DIR`. A source can be normalized for predictable
25 FPS H.264 playback with:

```bash
ffmpeg -hide_banner -nostdin -i input.mp4 -map 0:v:0 -an \
  -vf 'fps=25,setsar=1' -c:v libx264 -preset fast -crf 23 \
  -maxrate 4M -bufsize 4M -pix_fmt yuv420p -g 50 -keyint_min 50 \
  -sc_threshold 0 -bf 0 -x264-params slices=1 -movflags +faststart \
  /absolute/path/to/playback-videos/input.mp4
```

Use the project-local `vss` CLI and the matching VSS operational skills to
deploy the resolved file, add the resulting RTSP source to VIOS, and manage
monitoring. Stopping monitoring does not require deleting the playback video.
