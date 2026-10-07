#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 the VSS application contributors
# SPDX-License-Identifier: Apache-2.0
"""Prepare RTSP-compatible copies and a pinned MediaMTX binary; publish nothing."""

import argparse
import hashlib
import platform
import subprocess
import tarfile
from pathlib import Path
from urllib.request import urlretrieve


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--person-video", type=Path, required=True)
    parser.add_argument("--woman-video", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in {"aarch64", "arm64"}:
        raise SystemExit(
            "This recipe pins the Linux ARM64 MediaMTX binary for DGX Spark"
        )
    if not (args.build_dir / "resolved.yml").is_file():
        raise SystemExit("Resolve the shared build first")
    binary = args.build_dir / "mediamtx"
    if not binary.exists():
        version = "1.21.1"
        archive = args.build_dir / "mediamtx.tar.gz"
        urlretrieve(
            f"https://github.com/bluenviron/mediamtx/releases/download/v{version}/mediamtx_v{version}_linux_arm64.tar.gz",
            archive,
        )
        expected = "6a3aa635fb60ea9b8d566ec306f0a42ff1b6b52a3942bc2baffbe55880d4c3dd"
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise SystemExit("MediaMTX archive checksum mismatch")
        with tarfile.open(archive) as tar, tar.extractfile("mediamtx") as source:
            binary.write_bytes(source.read())
        binary.chmod(0o755)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for source, name in [
        (args.person_video, "person_collapsing"),
        (args.woman_video, "woman_hailing"),
    ]:
        if not source.is_file():
            raise SystemExit(f"Video does not exist: {source}")
        destination = args.output_dir / f"{name}.mp4"
        # Refuse stale or partial copies rather than silently overwrite them.
        if destination.exists():
            raise SystemExit(f"Playback copy already exists: {destination}")
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
                str(source),
                "-map",
                "0:v:0",
                "-an",
                "-vf",
                "fps=25,setsar=1",
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
        print(f"Prepared {destination}", flush=True)


if __name__ == "__main__":
    main()
