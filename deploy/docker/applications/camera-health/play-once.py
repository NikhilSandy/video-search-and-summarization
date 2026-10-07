#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 the VSS application contributors
# SPDX-License-Identifier: Apache-2.0
"""Publish the supplied camera-health video once and save pipeline evidence."""

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def now():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def request(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=90) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(
            f"{req.get_method()} {url}: HTTP {error.code}: {error.read().decode()}"
        ) from error


def cli(*arguments):
    result = subprocess.run(
        ["vss", *arguments], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument(
        "--host", required=True, help="Playback host reachable from VSS containers"
    )
    parser.add_argument("--sensor-name", default="camera_health_29sep26")
    parser.add_argument(
        "--sensor-id", help="Reuse the sensor ID returned by a previous onboarding"
    )
    args = parser.parse_args()
    bundle = Path(__file__).resolve().parent
    build = args.build_dir.resolve()
    evidence = build / "playback-test"
    evidence.mkdir(mode=0o700, exist_ok=False)
    origin = cli("configure", "show")["base_url"].rstrip("/")
    request(origin + "/alert-bridge/health")
    runtime_config = json.loads((build / "runtime/camera-health.json").read_text())
    models = cli("configure", "show")["services"]["rt_vlm"]["models"]
    if len(models) != 1:
        raise RuntimeError("Expected one advertised shared VLM model")
    worker = subprocess.run(
        [
            "docker",
            "inspect",
            "vss-camera-health-events",
            "--format",
            "{{.State.Health.Status}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    if worker.stdout.strip() != "healthy":
        raise RuntimeError("Camera-health event worker must be healthy before playback")
    video = build / "camera_health.mp4"
    duration = float(
        json.loads(
            subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(video),
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )["format"]["duration"]
    )
    source = f"rtsp://{args.host}:8554/camera_health"
    processes = []
    handles = []
    result = {
        "started_at": now(),
        "sensor_name": args.sensor_name,
        "source_url": source,
        "video": str(video),
        "duration_seconds": duration,
        "model": models[0],
        "model_calls_per_window": 1,
    }

    def read_audit():
        audit_path = build / "event-evidence/windows.jsonl"
        if not audit_path.exists():
            return []
        started = datetime.fromisoformat(result["started_at"].replace("Z", "+00:00"))
        return [
            row
            for row in map(json.loads, audit_path.read_text().splitlines())
            if row["sensor_id"] == args.sensor_name
            and datetime.fromisoformat(row["processed_at"]) >= started
        ]

    def launch(name, command):
        handle = (evidence / f"{name}.log").open("w")
        handles.append(handle)
        process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT)
        processes.append(process)
        return process

    try:
        server = launch(
            "mediamtx", [str(build / "mediamtx"), str(bundle / "mediamtx.yml")]
        )
        time.sleep(1)
        if server.poll() is not None:
            raise RuntimeError(
                "MediaMTX did not start; check playback ports and its log"
            )
        request("http://127.0.0.1:9997/v3/paths/list")
        start_clock = time.monotonic()
        publisher = launch(
            "publisher",
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "info",
                "-re",
                "-i",
                str(video),
                "-map",
                "0:v:0",
                "-an",
                "-c:v",
                "copy",
                "-rtsp_transport",
                "tcp",
                "-f",
                "rtsp",
                source,
            ],
        )
        deadline = time.monotonic() + 10
        while True:
            paths = request("http://127.0.0.1:9997/v3/paths/list")
            if any(
                p["name"] == "camera_health" and p.get("ready") for p in paths["items"]
            ):
                break
            if publisher.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Publisher did not expose a ready RTSP source")
            time.sleep(0.2)
        reader = launch(
            "reader",
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "info",
                "-rtsp_transport",
                "tcp",
                "-i",
                source,
                "-map",
                "0:v:0",
                "-an",
                "-c:v",
                "copy",
                "-f",
                "null",
                "-",
            ],
        )
        # Source onboarding is explicitly requested by supplying the video.
        added = (
            {"name": args.sensor_name, "sensor_id": args.sensor_id, "added": False}
            if args.sensor_id
            else cli(
                "vios",
                "add",
                source,
                "--name",
                args.sensor_name,
                "--type",
                "stream",
                "--raw",
            )
        )
        result["sensor"] = added
        payload = dict(runtime_config["params"])
        payload.update(
            model=models[0],
            live_stream_url=source,
            sensor_id=added["sensor_id"],
            sensor_name=args.sensor_name,
            alert_type=runtime_config["alert_type"],
        )
        result["rule"] = request(origin + "/alert-bridge/api/v1/realtime", payload)
        (evidence / "rule.json").write_text(json.dumps(result["rule"], indent=2) + "\n")
        print(
            f"Camera Health rule admitted for {args.sensor_name}; single-pass playback running",
            flush=True,
        )
        last_update = 0
        while publisher.poll() is None:
            elapsed = time.monotonic() - start_clock
            if elapsed > duration + 30:
                raise RuntimeError("Publisher exceeded the single-pass deadline")
            if elapsed - last_update >= 30:
                rows = read_audit()
                print(
                    json.dumps(
                        {
                            "playback_seconds": round(elapsed),
                            "processed_windows": len(rows),
                            "positive_windows": sum(
                                r["status"] == "valid" and r["class"] != "healthy"
                                for r in rows
                            ),
                            "invalid_windows": sum(
                                r["status"] != "valid" for r in rows
                            ),
                        }
                    ),
                    flush=True,
                )
                last_update = elapsed
                if elapsed >= 90 and not rows:
                    raise RuntimeError(
                        "No camera-health classifications arrived within 90 seconds"
                    )
            time.sleep(0.5)
        result["publisher_exit"] = publisher.returncode
        result["playback_seconds"] = round(time.monotonic() - start_clock, 3)
        if publisher.returncode:
            raise RuntimeError(f"Publisher failed with exit {publisher.returncode}")
        result["reader_exit"] = reader.wait(timeout=15)
        if result["reader_exit"]:
            raise RuntimeError("Independent RTSP reader failed")
        result["paths_after_eos"] = request("http://127.0.0.1:9997/v3/paths/list")
        if any(p.get("ready") for p in result["paths_after_eos"]["items"]):
            raise RuntimeError("RTSP source is still playing after EOF")
        print(
            "Video reached EOS; waiting for queued inference and indexing", flush=True
        )
        # The decoder can publish an empty terminal caption after its EOF grace.
        # Include that late validation failure in finite-video test evidence.
        time.sleep(35)
        rows = read_audit()
        result["classification_windows"] = len(rows)
        result["invalid_windows"] = sum(row["status"] != "valid" for row in rows)
        if not any(row["status"] == "valid" for row in rows):
            raise RuntimeError(
                "Playback produced no valid camera-health classifications"
            )
        query = urlencode(
            {
                "sensor_id": args.sensor_name,
                "start_time": result["started_at"],
                "end_time": now(),
                "limit": 1000,
            }
        )
        result["incidents"] = request(
            origin + "/alert-bridge/api/v1/realtime/incidents?" + query
        )
        result["ended_at"] = now()
        print(
            json.dumps(
                {
                    "incidents": result["incidents"]["total"],
                    "categories": sorted(
                        {i["category"] for i in result["incidents"]["incidents"]}
                    ),
                }
            ),
            flush=True,
        )
    except BaseException as error:
        result["error"] = str(error)
        raise
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        for handle in handles:
            handle.close()
        (evidence / "results.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
