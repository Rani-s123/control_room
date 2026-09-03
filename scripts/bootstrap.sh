#!/usr/bin/env bash
# One command from a fresh clone to a running control room.
set -euo pipefail
[ -f .env ] || { echo "copy .env.example to .env and fill it in first"; exit 1; }
set -a; source .env; set +a

echo "==> installing"
pip install -r requirements.txt

echo "==> creating schema"
python - <<'PY'
import os, clickhouse_connect, pathlib
c = clickhouse_connect.get_client(
    host=os.environ["CLICKHOUSE_HOST"], port=int(os.environ.get("CLICKHOUSE_PORT", 8443)),
    username=os.environ.get("CLICKHOUSE_USER","default"), password=os.environ["CLICKHOUSE_PASSWORD"],
    secure=os.environ.get("CLICKHOUSE_SECURE","true").lower()=="true")
for stmt in pathlib.Path("sql/01_schema.sql").read_text().split(";"):
    body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()
    if body: c.command(body)
print("schema ready")
PY

echo "==> loading telemetry (5M events, ~2 min)"
python data/generate_events.py --rows 5000000 --window-min 180

echo "==> serving on http://localhost:8080"
uvicorn controlroom.server:app --host 0.0.0.0 --port 8080
