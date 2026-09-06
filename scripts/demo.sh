#!/usr/bin/env bash
# Run the whole product with no cloud account and no credentials.
# Embedded ClickHouse, seeded telemetry, offline reasoning fallback.
set -euo pipefail
cd "$(dirname "$0")/.."

export DEMO_MODE=1
export DEMO_ROWS="${DEMO_ROWS:-250000}"
export DEMO_SCENARIO="${DEMO_SCENARIO:-shield_eviction}"

DEPS=(chdb numpy fastapi "uvicorn[standard]" Pillow)

# Debian, Ubuntu and Homebrew now mark the system interpreter as externally
# managed (PEP 668), where a bare `pip install` aborts. Prefer a local venv —
# it keeps the machine clean either way — and fall back to the current
# interpreter when the venv module is not available.
if [ -d .venv ]; then
  PY=".venv/bin/python"
elif python3 -m venv .venv 2>/dev/null; then
  PY=".venv/bin/python"
  echo "==> created .venv"
else
  PY="python3"
  echo "==> no venv available, installing into the current interpreter"
fi

echo "==> installing dependencies"
"$PY" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
"$PY" -m pip install -q "${DEPS[@]}" \
  || "$PY" -m pip install -q --break-system-packages "${DEPS[@]}"

echo "==> demo mode: embedded ClickHouse, ${DEMO_ROWS} events, scenario ${DEMO_SCENARIO}"
echo "==> http://localhost:8080"
exec "$PY" -m uvicorn controlroom.server:app --host 0.0.0.0 --port 8080
