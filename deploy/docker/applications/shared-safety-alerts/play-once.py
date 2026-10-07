#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 the VSS application contributors
# SPDX-License-Identifier: Apache-2.0
"""Run two independent alert applications through one shared VSS deployment.

Publish each input once. There are no publisher restarts, loop flags, or
on-demand hooks. Save the API responses and EOS evidence in the ignored build.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yaml


def now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def request(url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=45) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(
            f"{req.method} {url}: HTTP {error.code}: {error.read().decode()}"
        ) from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--person-video", type=Path, required=True)
    parser.add_argument("--woman-video", type=Path, required=True)
    parser.add_argument(
        "--host", required=True, help="VSS host address reachable from the containers"
    )
    args = parser.parse_args()
    bundle = Path(__file__).resolve().parent
    repo = bundle.parents[3]
    build = args.build_dir.resolve()
    evidence = build / "playback-test"
    # A second run needs a new evidence directory. Never replay automatically.
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    vss = ["uv", "run", "--project", str(repo / "libs/vss"), "vss"]

    def cli(*arguments: str) -> dict:
        result = subprocess.run(
            vss + list(arguments), capture_output=True, text=True, check=False
        )
        if result.returncode:
            raise RuntimeError(
                f"vss {' '.join(arguments)} failed: {result.stderr.strip()}"
            )
        return json.loads(result.stdout)

    configured = cli("configure", "show")
    origin = configured["base_url"].rstrip("/")
    alerts = origin + "/alert-bridge/api/v1"
    models = configured["services"]["rt_vlm"]["models"]
    if len(models) != 1:
        raise RuntimeError("Expected one shared advertised VLM model")
    request(origin + "/alert-bridge/health")
    hailing = yaml.safe_load(
        (bundle.parent / "woman-hailing/realtime-config.yml").read_text()
    )["always_on_rules"][0]
    collapse = json.loads((bundle / "person-collapse.json").read_text())
    applications = [
        {
            "name": "bundle_person_collapsing",
            "path": "person_collapsing",
            "video": args.person_video.resolve(),
            "alert_type": collapse["alert_type"],
            "params": collapse["params"],
        },
        {
            "name": "bundle_woman_hailing",
            "path": "woman_hailing",
            "video": args.woman_video.resolve(),
            "alert_type": hailing["alert_type"],
            "params": hailing["always_on_params"],
        },
    ]
    for app in applications:
        if not app["video"].is_file():
            raise RuntimeError(f"Video does not exist: {app['video']}")
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(app["video"]),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        app["duration_seconds"] = float(json.loads(probe.stdout)["format"]["duration"])

    server_log = (evidence / "mediamtx.log").open("w")
    server = subprocess.Popen(
        [str(build / "mediamtx"), str(bundle / "mediamtx.yml")],
        cwd=build,
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    publishers: list[tuple[dict, subprocess.Popen, object]] = []
    readers: list[tuple[dict, subprocess.Popen, object]] = []
    result: dict = {
        "started_at": now(),
        "model": models[0],
        "applications": [],
        "errors": [],
    }
    try:
        time.sleep(1)
        if server.poll() is not None:
            raise RuntimeError(
                "RTSP server exited; inspect mediamtx.log (ports may be occupied)"
            )
        request("http://127.0.0.1:9997/v3/paths/list")
        for app in applications:
            app["source_url"] = f"rtsp://{args.host}:8554/{app['path']}"
            app["started_at"] = now()
            app["start_clock"] = time.monotonic()
            log = (evidence / f"{app['path']}-publisher.log").open("w")
            command = [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "info",
                "-re",
                "-i",
                str(app["video"]),
                "-map",
                "0:v:0",
                "-an",
                "-c:v",
                "copy",
                "-rtsp_transport",
                "tcp",
                "-f",
                "rtsp",
                app["source_url"],
            ]
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            app["publisher_command"] = command
            publishers.append((app, process, log))
        # Only server readiness is polled; failed VSS calls are never retried.
        ready_deadline = time.monotonic() + 10
        while True:
            paths = request("http://127.0.0.1:9997/v3/paths/list")
            ready = {p["name"] for p in paths["items"] if p.get("ready")}
            if all(app["path"] in ready for app in applications):
                break
            if time.monotonic() >= ready_deadline:
                raise RuntimeError("The two RTSP publishers did not become ready")
            time.sleep(0.2)
        # An independent RTSP reader must exit on EOS, proving that the server
        # disconnects readers rather than silently restarting either file.
        for app in applications:
            log = (evidence / f"{app['path']}-reader.log").open("w")
            command = [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "info",
                "-rtsp_transport",
                "tcp",
                "-i",
                app["source_url"],
                "-map",
                "0:v:0",
                "-an",
                "-c:v",
                "copy",
                "-f",
                "null",
                "-",
            ]
            readers.append(
                (
                    app,
                    subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT),
                    log,
                )
            )

        def onboard(app: dict) -> dict:
            added = cli(
                "vios",
                "add",
                app["source_url"],
                "--name",
                app["name"],
                "--type",
                "stream",
                "--raw",
            )
            payload = dict(app["params"])
            payload.update(
                model=models[0],
                live_stream_url=app["source_url"],
                sensor_id=added["sensor_id"],
                sensor_name=app["name"],
                alert_type=app["alert_type"],
            )
            # The direct source is deliberate: live inference receives RTSP EOS
            # even if the VIOS recording proxy reconnects an offline camera.
            created = request(alerts + "/realtime", payload)
            return {
                "name": app["name"],
                "alert_type": app["alert_type"],
                "source_url": app["source_url"],
                "sensor_id": added["sensor_id"],
                "rule": created,
                "params": payload,
                "video": str(app["video"]),
                "started_at": app["started_at"],
                "publisher_command": app["publisher_command"],
            }

        with ThreadPoolExecutor(max_workers=2) as pool:
            result["applications"] = list(pool.map(onboard, applications))
        (evidence / "rules-during-playback.json").write_text(
            json.dumps(request(alerts + "/realtime"), indent=2)
        )
        print(
            "Both saved camera-specific rules are running on the shared VLM", flush=True
        )
        last_update = 0.0
        while any(process.poll() is None for _, process, _ in publishers):
            for app, process, _ in publishers:
                if (
                    process.poll() is None
                    and time.monotonic() - app["start_clock"]
                    > app["duration_seconds"] + 30
                ):
                    raise RuntimeError(
                        f"{app['name']}: publisher exceeded the single-pass playback deadline"
                    )
            for app, process, _ in publishers:
                if process.poll() is not None and "ended_at" not in app:
                    app.update(
                        ended_at=now(),
                        publisher_exit=process.returncode,
                        playback_seconds=round(
                            time.monotonic() - app["start_clock"], 3
                        ),
                    )
                    print(
                        f"{app['name']}: publisher ended (exit {process.returncode}); no restart",
                        flush=True,
                    )
            if time.monotonic() - last_update >= 20:
                print(
                    "Playback progress: "
                    + ", ".join(
                        f"{a['name']}={'EOS' if p.poll() is not None else 'playing'}"
                        for a, p, _ in publishers
                    ),
                    flush=True,
                )
                last_update = time.monotonic()
            time.sleep(0.5)
        for app, process, _ in publishers:
            if "ended_at" not in app:
                app.update(
                    ended_at=now(),
                    publisher_exit=process.returncode,
                    playback_seconds=round(time.monotonic() - app["start_clock"], 3),
                )
            if process.returncode:
                raise RuntimeError(
                    f"{app['name']}: publisher failed with exit {process.returncode}"
                )
        for app, reader, _ in readers:
            try:
                reader.wait(timeout=10)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    f"RTSP reader did not receive EOS for {app['name']}"
                ) from error
            app["reader_exit"] = reader.returncode
        final_paths = request("http://127.0.0.1:9997/v3/paths/list")
        (evidence / "rtsp-paths-after-eos.json").write_text(
            json.dumps(final_paths, indent=2)
        )
        if any(p.get("ready") for p in final_paths["items"]):
            raise RuntimeError(
                "An RTSP path is still publishing after both videos ended"
            )
        print("Both RTSP sources reached EOS; independent readers exited", flush=True)
        # Let already queued model work and indexing finish, with bounded waits.
        time.sleep(20)
        for saved, app in zip(result["applications"], applications):
            saved.update(
                {
                    k: app[k]
                    for k in (
                        "ended_at",
                        "publisher_exit",
                        "reader_exit",
                        "playback_seconds",
                    )
                }
            )
            query = urlencode(
                {
                    "sensor_id": app["name"],
                    "category": app["alert_type"],
                    "start_time": result["started_at"],
                    "end_time": now(),
                    "limit": 100,
                    "consolidate": "true",
                }
            )
            incidents = request(alerts + "/realtime/incidents?" + query)
            saved["incidents"] = incidents
            print(
                f"{app['name']}: {incidents.get('total')} consolidated events",
                flush=True,
            )
        result["rules_after_eos"] = request(alerts + "/realtime")
        result["eos_verified"] = True
        result["finished_at"] = now()
    except BaseException as error:
        result["errors"].append(str(error))
        raise
    finally:
        for _, process, log in publishers + readers:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            log.close()
        server.send_signal(signal.SIGINT)
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        server_log.close()
        (evidence / "results.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
