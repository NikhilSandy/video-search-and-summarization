#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 the VSS application contributors
# SPDX-License-Identifier: Apache-2.0
"""Prepare a camera-health runtime overlay and playback copy; change no services."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

PINNED_IMAGE = "ghcr.io/nvidia-ai-blueprints/vss/vss-rt-vlm:develop-4c432f24cf39-sbsa"
MEDIAMTX_SHA256 = "08d61a4c07d8cf124e037a86af09e616fce132e9c1edbbc673caa72cf9794810"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-compose", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--mediamtx", type=Path, required=True)
    args = parser.parse_args()
    bundle = Path(__file__).resolve().parent
    repo = bundle.parents[3]
    build = args.build_dir.resolve()
    base = args.base_compose.resolve()
    if (repo / "_builds").resolve() not in build.parents:
        raise SystemExit(
            "Build directory must be under the repository's ignored _builds"
        )
    if not args.video.is_file() or not args.mediamtx.is_file():
        raise SystemExit("The input video and prepared MediaMTX binary must exist")
    if hashlib.sha256(args.mediamtx.read_bytes()).hexdigest() != MEDIAMTX_SHA256:
        raise SystemExit("Expected the pinned Linux ARM64 MediaMTX 1.21.1 binary")
    compose = yaml.safe_load(base.read_text())
    runtime = compose["services"]["rtvi-vlm"]
    if runtime["image"] != PINNED_IMAGE:
        raise SystemExit("This overlay requires the validated pinned SBSA RT-VLM image")
    # A missing diagnostics topic blocks RT-VLM's Kafka sender during disconnects.
    error_topic = runtime["environment"].get("ERROR_MESSAGE_TOPIC", "mdx-vlm-errors")
    for service in compose["services"].values():
        environment = service.get("environment", {})
        if "KAFKA_TOPICS" in environment:
            topics = json.loads(environment["KAFKA_TOPICS"])
            if error_topic and not any(t["name"] == error_topic for t in topics):
                topics.append({"name": error_topic})
            environment["KAFKA_TOPICS"] = json.dumps(topics)
    build.mkdir(mode=0o700, parents=True, exist_ok=False)
    runtime_dir = build / "runtime"
    runtime_dir.mkdir(mode=0o755)
    for source in (
        bundle / "events.py",
        bundle / "camera-health.json",
        repo / "services/rtvi/rt-vlm/src/server/camera_health.py",
    ):
        target = runtime_dir / source.name
        shutil.copyfile(source, target)
        target.chmod(0o644)
    probe_source = repo / "services/rtvi/rt-vlm/src/utils/media_file_info.py"
    probe_snapshot = runtime_dir / "media_file_info.py"
    shutil.copyfile(probe_source, probe_snapshot)
    probe_snapshot.chmod(0o644)
    runtime["environment"]["RTVI_RTSP_PROBE_BACKEND"] = "ffprobe"
    # This SBSA host reports no NVDEC engines; its hardware RTSP decoder blocks.
    # Decode H.264/H.265 on CPU and retain the shared GPU conversion/model path.
    ranks = runtime["environment"].get("GST_PLUGIN_FEATURE_RANK", "")
    rank_map = dict(item.rsplit(":", 1) for item in ranks.split(",") if item)
    rank_map.update(nvv4l2decoder="0", avdec_h264="512", avdec_h265="512")
    runtime["environment"]["GST_PLUGIN_FEATURE_RANK"] = ",".join(
        f"{name}:{rank}" for name, rank in rank_map.items()
    )
    runtime.setdefault("volumes", []).append(
        {
            "type": "bind",
            "source": str(probe_snapshot),
            "target": "/opt/nvidia/rtvi/rtvi/utils/media_file_info.py",
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    )
    codec_source = repo / "services/rtvi/rt-vlm/src/scripts/install_codecs_nonroot.sh"
    codec_snapshot = runtime_dir / "install_codecs_nonroot.sh"
    shutil.copyfile(codec_source, codec_snapshot)
    codec_snapshot.chmod(0o644)
    runtime["volumes"].append(
        {
            "type": "bind",
            "source": str(codec_snapshot),
            "target": "/opt/nvidia/rtvi/install_codecs_nonroot.sh",
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    )
    evidence = build / "event-evidence"
    evidence.mkdir(mode=0o755)
    # The default ten-second bridge timeout is shorter than Gst discovery's
    # fifteen-second bound. Give admission time to finish probing this source.
    bridge = compose["services"]["alert-bridge"]
    bridge_mount = next(
        v for v in bridge["volumes"] if v.get("target") == "/app/configs/config.yml"
    )
    bridge_config = yaml.safe_load(Path(bridge_mount["source"]).read_text())
    bridge_config["rtvi_vlm"]["timeout"] = max(
        45, bridge_config["rtvi_vlm"].get("timeout", 30)
    )
    bridge_snapshot = runtime_dir / "alert-config.yml"
    bridge_snapshot.write_text(yaml.safe_dump(bridge_config, sort_keys=False))
    bridge_snapshot.chmod(0o644)
    bridge_mount["source"] = str(bridge_snapshot)
    if "camera-health-events" in compose["services"]:
        raise SystemExit("Base already includes camera-health-events")
    compose["services"]["camera-health-events"] = {
        "image": PINNED_IMAGE,
        "container_name": "vss-camera-health-events",
        "entrypoint": ["python3", "-u", "/camera-health/events.py"],
        "user": f"{os.getuid()}:{os.getgid()}",
        "restart": "unless-stopped",
        "read_only": True,
        "tmpfs": ["/tmp"],
        "mem_limit": "512m",
        "cpus": 1,
        "environment": {
            "PYTHONPATH": "/opt/nvidia/rtvi/rtvi",
            "KAFKA_BOOTSTRAP_SERVERS": runtime["environment"][
                "KAFKA_BOOTSTRAP_SERVERS"
            ],
            "CAMERA_HEALTH_CAPTIONS_TOPIC": runtime["environment"]["MESSAGE_BUS_TOPIC"],
            "CAMERA_HEALTH_INCIDENTS_TOPIC": runtime["environment"][
                "KAFKA_INCIDENT_TOPIC"
            ],
            "CAMERA_HEALTH_CONSUMER_GROUP": "vss-camera-health-v1",
        },
        "networks": {"default": None},
        "volumes": [
            {
                "type": "bind",
                "source": str(runtime_dir),
                "target": "/camera-health",
                "read_only": True,
                "bind": {"create_host_path": False},
            },
            {
                "type": "bind",
                "source": str(runtime_dir / "camera_health.py"),
                "target": "/opt/nvidia/rtvi/rtvi/server/camera_health.py",
                "read_only": True,
                "bind": {"create_host_path": False},
            },
            {
                "type": "bind",
                "source": str(evidence),
                "target": "/evidence",
                "bind": {"create_host_path": False},
            },
        ],
        "healthcheck": {
            "test": [
                "CMD",
                "python3",
                "-c",
                (
                    "import pathlib,time; p=pathlib.Path('/tmp/camera-health-ready'); "
                    "assert p.exists() and time.time()-p.stat().st_mtime < 30"
                ),
            ],
            "interval": "5s",
            "timeout": "3s",
            "retries": 6,
            "start_period": "15s",
        },
    }
    resolved = build / "resolved.yml"
    resolved.write_text(yaml.safe_dump(compose, sort_keys=False))
    resolved.chmod(0o600)
    subprocess.run(
        ["docker", "compose", "-f", str(resolved), "config", "-q"], check=True
    )
    # Reuse the pinned single-pass playback server.
    shutil.copyfile(args.mediamtx, build / "mediamtx")
    (build / "mediamtx").chmod(0o755)
    destination = build / "camera_health.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-n",
            "-threads",
            "4",
            "-i",
            str(args.video.resolve()),
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            "scale=960:540,fps=25,setsar=1",
            "-c:v",
            "libx264",
            "-threads",
            "4",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-maxrate",
            "4M",
            "-bufsize",
            "4M",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "50",
            "-keyint_min",
            "50",
            "-sc_threshold",
            "0",
            "-bf",
            "0",
            "-x264-params",
            "slices=1",
            "-movflags",
            "+faststart",
            str(destination),
        ],
        check=True,
    )
    manifest = {
        "base_compose": str(base),
        "video_source": str(args.video.resolve()),
        "playback_video": str(destination),
        "image": PINNED_IMAGE,
        "model_calls_per_window": 1,
        "classification_policy": "single_most_likely",
        "max_incidents_per_window": 1,
        "runtime_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in runtime_dir.iterdir()
        },
        "mediamtx_sha256": hashlib.sha256(
            (build / "mediamtx").read_bytes()
        ).hexdigest(),
    }
    (build / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Prepared {resolved}; playback: {destination}", flush=True)


if __name__ == "__main__":
    main()
