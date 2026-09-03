#!/usr/bin/env bash
# Run the whole product with no cloud account and no credentials.
# Embedded ClickHouse, seeded telemetry, offline reasoning fallback.
set -euo pipefail
export DEMO_MODE=1
export DEMO_ROWS="${DEMO_ROWS:-250000}"
export DEMO_SCENARIO="${DEMO_SCENARIO:-shield_eviction}"
pip install -q chdb numpy fastapi "uvicorn[standard]" Pillow
echo "==> demo mode: embedded ClickHouse, ${DEMO_ROWS} events, scenario ${DEMO_SCENARIO}"
echo "==> http://localhost:8080"
exec uvicorn controlroom.server:app --host 0.0.0.0 --port 8080
