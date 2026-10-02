#!/bin/bash
# Cron-safe wrapper: no module system needed, logs to logs/.
cd "$(dirname "$0")" || exit 1
export LD_LIBRARY_PATH=/cosma/local/Python/3.12.4/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
mkdir -p logs
exec .venv/bin/python -m jobhound.main "$@" >> "logs/$(date +%Y-%m).log" 2>&1
