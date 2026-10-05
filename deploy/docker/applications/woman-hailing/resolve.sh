#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-FileCopyrightText: 2026 the VSS application contributors
# SPDX-License-Identifier: Apache-2.0
# Adapted from the vss-build-vision-ai Compose resolution workflow.
# Only write a new ignored build; do not start, stop, or restart services.
set -euo pipefail
umask 077

VSS_EXAMPLE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VSS_REPO_ROOT="$(git -C "$VSS_EXAMPLE_DIR" rev-parse --show-toplevel)"
: "${HOST_IP:?Export the reachable server IPv4 address}"
: "${VSS_DATA_DIR:?Export an absolute path for persistent application data}"
: "${NVSTREAMER_ALERTS_VIDEO_DIR:?Export an absolute directory containing the playback MP4}"
: "${NGC_CLI_API_KEY:?Export NGC_CLI_API_KEY from a protected credentials file}"
export HOST_IP VSS_DATA_DIR NVSTREAMER_ALERTS_VIDEO_DIR NGC_CLI_API_KEY
export NGC_API_KEY="$NGC_CLI_API_KEY"
export VSS_APPS_DIR="$VSS_REPO_ROOT/deploy/docker"
export BUILD_DIR="${BUILD_DIR:-$VSS_REPO_ROOT/_builds/woman-hailing-example}"
# Export before containers.env is read, so image tags do not use develop-latest.
export VSS_CONTAINER_TAG=develop-4c432f24cf39
export VSS_CONTAINER_TAG_SUFFIX=-sbsa

python3 - "$VSS_REPO_ROOT" <<'PY'
import ipaddress
import json
import os
import sys
from pathlib import Path

repo = Path(sys.argv[1])
ipaddress.IPv4Address(os.environ["HOST_IP"])
build = Path(os.environ["BUILD_DIR"])
ignored = (repo / "_builds").resolve()
if not build.is_absolute() or ignored not in build.resolve().parents:
    raise SystemExit("BUILD_DIR must be inside this checkout's ignored _builds directory")
for key in ("VSS_DATA_DIR", "NVSTREAMER_ALERTS_VIDEO_DIR"):
    if not Path(os.environ[key]).is_absolute():
        raise SystemExit(f"{key} must be an absolute path")
if not Path(os.environ["NVSTREAMER_ALERTS_VIDEO_DIR"]).is_dir():
    raise SystemExit("The playback directory must already exist")

# Refuse to overwrite any existing deployment or previously resolved credentials.
build.parent.mkdir(parents=True, exist_ok=True)
build.mkdir(mode=0o700, exist_ok=False)
configs = repo / "deploy/docker/services"
nvstreamer = json.loads((configs / "nvstreamer/configs/vst-config.json").read_text())
nvstreamer["network"]["server_domain_name"] = os.environ["HOST_IP"]
nvstreamer["network"]["rtsp_server_instances_count"] = 1
vios = json.loads((configs / "vios/configs/vst_config.json").read_text())
vios["network"]["rtsp_streaming_over_tcp"] = True
vios["data"]["enable_proxy_server_sei_metadata"] = False
for relative, document in (("nvstreamer/vst-config.json", nvstreamer),
                           ("vios/vst_config.json", vios)):
    destination = build / relative
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    destination.write_text(json.dumps(document, indent=2) + "\n")
    destination.chmod(0o644)  # Non-secret configuration read by container users.
PY

VSS_FOUNDATION_DIR="$VSS_APPS_DIR/developer-profiles/dev-profile-alerts"
docker compose \
  --env-file "$VSS_APPS_DIR/containers.env" \
  --env-file "$VSS_FOUNDATION_DIR/.env" \
  --env-file "$VSS_FOUNDATION_DIR/overrides.env" \
  --env-file "$VSS_EXAMPLE_DIR/app.env" \
  -f "$VSS_EXAMPLE_DIR/compose.yml" config --no-consistency > "$BUILD_DIR/resolved.yml"
python3 "$VSS_REPO_ROOT/skills/vss-build-vision-ai/scripts/normalize_resolved_yml.py" "$BUILD_DIR/resolved.yml"
python3 "$VSS_REPO_ROOT/skills/vss-build-vision-ai/scripts/validate_resolved_yml.py" \
  "$BUILD_DIR/resolved.yml" --repo-root "$VSS_REPO_ROOT" --expect-container-tag "$VSS_CONTAINER_TAG"
chmod 600 "$BUILD_DIR/resolved.yml"
printf 'Resolved configuration: %s/resolved.yml\nNo services were changed.\n' "$BUILD_DIR"
