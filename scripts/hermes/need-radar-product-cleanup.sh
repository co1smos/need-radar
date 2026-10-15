#!/usr/bin/env bash
set -euo pipefail

project_root="${NEED_RADAR_PROJECT_ROOT:-/home/ubuntu/projects/need-radar}"
output_root="${NEED_RADAR_OUTPUT_ROOT:-${HOME}/.local/state/need-radar/product-cycle/runs}"
cd "$project_root"
exec python3 -m need_radar.product cleanup --output-root "$output_root" "$@"
