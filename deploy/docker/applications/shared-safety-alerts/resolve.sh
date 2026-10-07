#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 the VSS application contributors
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
umask 077

VSS_BUNDLE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VSS_REPO_ROOT="$(git -C "$VSS_BUNDLE_DIR" rev-parse --show-toplevel)"
: "${BASE_BUILD_DIR:?Set BASE_BUILD_DIR to the existing woman-hailing build}"
: "${BUILD_DIR:?Set BUILD_DIR to a new directory under _builds}"
: "${NGC_CLI_API_KEY:?Export NGC_CLI_API_KEY from the protected credentials file}"
export BASE_BUILD_DIR BUILD_DIR NGC_CLI_API_KEY
export NGC_API_KEY="$NGC_CLI_API_KEY"
export VSS_CONTAINER_TAG=develop-4c432f24cf39
export VSS_CONTAINER_TAG_SUFFIX=-sbsa

python3 - "$VSS_REPO_ROOT" "$VSS_BUNDLE_DIR" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

repo, bundle = map(Path, sys.argv[1:])
base = Path(os.environ['BASE_BUILD_DIR']).resolve()
build = Path(os.environ['BUILD_DIR']).resolve()
if (repo / '_builds').resolve() not in build.parents or base == build:
    raise SystemExit('BUILD_DIR must be a separate ignored build directory')
build.mkdir(mode=0o700, parents=True, exist_ok=True)
if (build / 'resolved.yml').exists():
    raise SystemExit('Refusing to overwrite an already resolved build')
values = {}
for filename in (base / 'override.env', bundle / 'bundle.env'):
    for line in filename.read_text().splitlines():
        if re.match(r'^[A-Z][A-Z0-9_]*=', line):
            key, value = line.split('=', 1)
            values[key] = value
if values.get('FOUNDATION') != 'alerts' or values.get('MODE') != '2d_vlm':
    raise SystemExit('The base build must be a real-time VLM alerts deployment')
profiles = values['COMPOSE_PROFILES'].split(',')
values['COMPOSE_PROFILES'] = ','.join(p for p in profiles if p != 'nvstreamer-alerts')
values['BUILD_DIR'] = str(build)
values['REQUESTED_PROFILES'] = ''
patches = build / 'patches'
patches.mkdir(mode=0o700, exist_ok=True)
notifications = json.loads((repo / 'deploy/docker/developer-profiles/dev-profile-alerts/vios/configs/notification_config_2d_vlm.json').read_text())
for item in notifications['webhooks']['items']:
    item['enabled'] = False
notification_path = patches / 'notification_config.json'
notification_path.write_text(json.dumps(notifications, indent=2) + '\n')
notification_path.chmod(0o644)
values['VST_NOTIFICATION_CONFIG_PATH'] = str(notification_path)
(build / 'override.env').write_text('\n'.join(f'{k}={v}' for k, v in values.items()) + '\n')
# Preserve the transport settings and host mappings of the existing stack.
includes = [str(repo / 'deploy/docker/compose.yml')]
for name in ('runtime-hosts.yml', 'vios-transport.yml'):
    path = base / 'patches' / name
    if path.is_file():
        includes.append(str(path))
(build / 'compose.yml').write_text('include:\n  - path:\n' + ''.join('      - ' + json.dumps(p) + '\n' for p in includes))
print(f'Reusing the existing Compose project: {values["COMPOSE_PROJECT_NAME"]}')
PY

VSS_FOUNDATION_DIR="$VSS_REPO_ROOT/deploy/docker/developer-profiles/dev-profile-alerts"
docker compose \
  --env-file "$VSS_REPO_ROOT/deploy/docker/containers.env" \
  --env-file "$VSS_FOUNDATION_DIR/.env" \
  --env-file "$VSS_FOUNDATION_DIR/overrides.env" \
  --env-file "$BUILD_DIR/override.env" \
  -f "$BUILD_DIR/compose.yml" config --no-consistency > "$BUILD_DIR/resolved.yml"
python3 "$VSS_REPO_ROOT/skills/vss-build-vision-ai/scripts/normalize_resolved_yml.py" "$BUILD_DIR/resolved.yml"
python3 "$VSS_REPO_ROOT/skills/vss-build-vision-ai/scripts/validate_resolved_yml.py" \
  "$BUILD_DIR/resolved.yml" --repo-root "$VSS_REPO_ROOT" --expect-container-tag "$VSS_CONTAINER_TAG"
chmod 600 "$BUILD_DIR/override.env" "$BUILD_DIR/resolved.yml"
printf 'Resolved shared stack: %s/resolved.yml\n' "$BUILD_DIR"
