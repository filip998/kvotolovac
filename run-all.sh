#!/usr/bin/env bash
# Run both backend and frontend in parallel.
# Press Ctrl+C to stop both.

trap 'kill 0' EXIT

bash "$(dirname "$0")/run-backend.sh" &
bash "$(dirname "$0")/run-frontend.sh" &

wait
