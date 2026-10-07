#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 the VSS application contributors
# SPDX-License-Identifier: Apache-2.0
"""Prepare and audit one accident video on an already running shared VSS stack."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen


def main():
    bundle = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--host', required=True, help='RTSP publisher address reachable by VSS')
    parser.add_argument('--mediamtx', type=Path, required=True)
    parser.add_argument('--sensor-name', default='accident_detection_live')
    parser.add_argument('--application-config', type=Path, default=bundle / 'accident-detection.json')
    parser.add_argument('--preroll-seconds', type=int, default=12)
    parser.add_argument('--reuse-sensor', action='store_true', help='Reuse the existing named VIOS camera')
    args = parser.parse_args()
    if args.preroll_seconds < 1:
        parser.error('--preroll-seconds must be positive')
    for path in [args.video, args.application_config, args.mediamtx]:
        if not path.is_file():
            parser.error(f'Missing file: {path}')
    if not args.sensor_name.replace('_', '').replace('-', '').isalnum():
        parser.error('Use letters, numbers, underscores or hyphens for --sensor-name')
    configured = json.loads(subprocess.check_output(['vss', 'configure', 'show'], text=True))
    with urlopen(configured['base_url'].rstrip('/') + '/alert-bridge/api/v1/realtime', timeout=30) as response:
        rules = json.load(response)['rules']
    if any(rule.get('sensor_name') == args.sensor_name for rule in rules):
        parser.error('This sensor already has a saved rule; choose a fresh --sensor-name for a new test')
    sensor = None
    if args.reuse_sensor:
        sensor = json.loads(subprocess.check_output([
            'vss', 'vios', 'timeline', '--sensor', args.sensor_name, '--raw'
        ], text=True))
        if sensor['name'] != args.sensor_name or sensor['type'] != 'stream':
            raise RuntimeError('The existing camera name or provenance does not match')
    detector = json.loads(args.application_config.read_text())
    models = configured['services']['rt_vlm']['models']
    if len(models) != 1:
        raise RuntimeError('Expected one shared advertised VLM model')
    detector['params']['model'] = models[0]
    probe = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(args.video)
    ], text=True))
    duration = float(probe['format']['duration'])
    run = args.run_dir.resolve()
    run.mkdir(mode=0o700, parents=True, exist_ok=False)
    (run / 'detector.json').write_text(json.dumps(detector, indent=2) + '\n')
    (run / 'input.json').write_text(json.dumps({
        'origin': configured['base_url'].rstrip('/'),
        'source_url': f'rtsp://{args.host}:8554/{args.sensor_name}',
        'sensor_name': args.sensor_name,
        'existing_sensor_id': sensor['sensor_id'] if sensor else None,
        'original_video': str(args.video.resolve()),
        'original_duration_seconds': duration,
        'preroll_seconds': args.preroll_seconds,
        'source_duration_seconds': duration + args.preroll_seconds,
        'drain_timeout_seconds': 240,
    }, indent=2) + '\n')
    (run / 'mediamtx.yml').write_text(
        'logLevel: info\nrtspAddress: :8554\nrtspTransports: [tcp]\n'
        'rtmp: false\nhls: false\nwebrtc: false\nsrt: false\nmoq: false\n'
        'api: true\napiAddress: 127.0.0.1:9998\npaths:\n'
        f'  {args.sensor_name}:\n    source: publisher\n'
    )
    subprocess.run([
        'ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'error', '-n',
        '-threads', '4', '-i', str(args.video.resolve()), '-map', '0:v:0', '-an',
        '-vf', f'fps=25,setsar=1,tpad=start_mode=add:start_duration={args.preroll_seconds}:color=black',
        '-c:v', 'libx264', '-threads', '4', '-preset', 'fast', '-crf', '20',
        '-maxrate', '4M', '-bufsize', '4M', '-pix_fmt', 'yuv420p',
        '-g', '25', '-keyint_min', '25', '-sc_threshold', '0', '-bf', '0',
        '-x264-params', 'slices=1', '-movflags', '+faststart', str(run / 'source.mp4')
    ], check=True)
    subprocess.run([
        sys.executable, str(bundle / 'play-single-once.py'), '--run-dir', str(run),
        '--mediamtx', str(args.mediamtx.resolve())
    ], check=True)


if __name__ == '__main__':
    main()
