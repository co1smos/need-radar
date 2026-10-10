#!/usr/bin/env bash
set -euo pipefail

project_root="${NEED_RADAR_PROJECT_ROOT:-/home/ubuntu/projects/need-radar}"
config_path="${NEED_RADAR_CONFIG:-${project_root}/.need-radar/scheduled.json}"
cd "$project_root"
exec python3 -m need_radar.scheduled missed --config "$config_path"
