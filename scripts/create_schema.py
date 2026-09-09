"""Apply sql/01_schema.sql to the configured ClickHouse Cloud service.

Split out of scripts/bootstrap.sh so the PowerShell path runs the identical
step. Statements are applied in file order; the schema's CREATE ... IF NOT
EXISTS clauses make a re-run harmless.
"""

from __future__ import annotations

import os
import pathlib
import sys

import clickhouse_connect

host = os.environ.get("CLICKHOUSE_HOST")
if not host:
    sys.exit("CLICKHOUSE_HOST is not set - load .env first")

client = clickhouse_connect.get_client(
    host=host,
    port=int(os.environ.get("CLICKHOUSE_PORT", 8443)),
    username=os.environ.get("CLICKHOUSE_USER", "default"),
    password=os.environ["CLICKHOUSE_PASSWORD"],
    secure=os.environ.get("CLICKHOUSE_SECURE", "true").lower() == "true",
)

applied = 0
for stmt in pathlib.Path("sql/01_schema.sql").read_text().split(";"):
    body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()
    if body:
        client.command(body)
        applied += 1

print(f"schema ready - {applied} statements applied to {host}")
