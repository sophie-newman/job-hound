#!/bin/bash
# Cron-safe wrapper: runs with the project venv, logs to logs/.
cd "$(dirname "$0")" || exit 1
mkdir -p logs
exec .venv/bin/python -m jobhound.main "$@" >> "logs/$(date +%Y-%m).log" 2>&1
