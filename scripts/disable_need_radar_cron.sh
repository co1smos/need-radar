#!/usr/bin/env bash
set -u

if [[ $# -ne 2 ]]; then
  printf 'usage: %s MAIN_JOB_ID WATCHDOG_JOB_ID\n' "$0" >&2
  exit 2
fi

if [[ -z "${1-}" || -z "${2-}" || "$1" == "$2" ]]; then
  printf 'usage: %s MAIN_JOB_ID WATCHDOG_JOB_ID\n' "$0" >&2
  exit 2
fi

status=0
hermes cron pause "$1" || status=1
hermes cron pause "$2" || status=1
hermes cron list --all || status=1
exit "$status"
