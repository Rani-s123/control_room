# Deploy sheet — exact variables, exact checks

Requirement #1 is not "deployed". It is `/healthz` returning `"transport":"mcp"` on the
public URL. Everything below exists to get to that one line.

---

## 1. ClickHouse Cloud

Redeem the $400 hackathon credits, create a service, and copy these four values from
the service's **Connect** panel:

| Variable | Where it comes from | Example |
|---|---|---|
| `CLICKHOUSE_HOST` | Connect panel, host only — no `https://`, no port | `abc123.us-east-1.aws.clickhouse.cloud` |
| `CLICKHOUSE_PORT` | HTTPS port | `8443` |
| `CLICKHOUSE_USER` | usually unchanged | `default` |
| `CLICKHOUSE_PASSWORD` | shown once at service creation | |

Then load the schema and events (from `D:\control-room`, with `.env` filled):

```
bash scripts/bootstrap.sh
```

**Do NOT set `CLICKHOUSE_TRANSPORT`.** Leaving it unset is what selects the MCP
transport. Setting it to `direct` bypasses the MCP server and fails requirement #1.

---

## 2. Vertex AI / Gemini

| Variable | Value |
|---|---|
| `GOOGLE_GENAI_USE_VERTEXAI` | `TRUE` |
| `GOOGLE_CLOUD_PROJECT` | your GCP project id |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` |
| `GEMINI_REASONING_MODEL` | `gemini-2.5-pro` |
| `GEMINI_FAST_MODEL` | `gemini-2.5-flash` |

The runtime service account needs `roles/aiplatform.user`.

Without a GCP project, the fallback is an API key instead: set
`GOOGLE_GENAI_USE_VERTEXAI=FALSE` and `GOOGLE_API_KEY=<key from aistudio.google.com>`.
That still gives real Gemini and clears requirement #2, though Vertex reads better
against a "Built with Google Cloud" rule.

---

## 3. Paste block for Railway

Railway → your service → **Variables** → **Raw Editor** → paste, fill the blanks:

```
CLICKHOUSE_HOST=
CLICKHOUSE_PORT=8443
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=
CLICKHOUSE_SECURE=true
CLICKHOUSE_DATABASE=control_room
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_REASONING_MODEL=gemini-2.5-pro
GEMINI_FAST_MODEL=gemini-2.5-flash
INCIDENT_WINDOW_MIN=20
BASELINE_MIN=180
Z_THRESHOLD=2.0
ARPU_PER_VIEWER_HOUR_USD=0.42
```

Then **Settings → Networking → Generate Domain** for the public URL.

Vertex on Railway needs service-account credentials, which is awkward — if GCP auth
fights you there, use the `GOOGLE_API_KEY` route above, or deploy to Cloud Run
(next section) where Vertex auth is automatic.

---

## 4. Or Cloud Run (Vertex auth is free here)

```
gcloud run deploy control-room --source . ^
  --region %GOOGLE_CLOUD_LOCATION% --allow-unauthenticated ^
  --memory 2Gi --cpu 2 --timeout 300 ^
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=%GOOGLE_CLOUD_PROJECT%,GOOGLE_CLOUD_LOCATION=%GOOGLE_CLOUD_LOCATION%,CLICKHOUSE_HOST=%CLICKHOUSE_HOST%,CLICKHOUSE_USER=%CLICKHOUSE_USER%,CLICKHOUSE_SECURE=true,CLICKHOUSE_DATABASE=control_room" ^
  --set-secrets "CLICKHOUSE_PASSWORD=clickhouse-password:latest"
```

`--memory 2Gi` and `--timeout 300` are not optional. The Cloud Run defaults are
512 MiB and 60s; a full run streams for longer than that.

---

## 5. The two checks that decide the submission

Open these in a browser on the **public URL**, not localhost:

```
$URL/healthz
  → {"status":"ok","events_loaded":5000000,"transport":"mcp"}
```

```
$URL/api/mode
  → {"database":"ClickHouse Cloud via MCP",
     "transport":"mcp",
     "mcp_server":"mcp-clickhouse",
     "reasoning":"Gemini on Vertex AI",
     "degraded":false}
```

| What you see | What it means |
|---|---|
| `"transport":"mcp"` | requirement #1 satisfied |
| `"transport":"embedded"` | env vars did not load — `CLICKHOUSE_HOST` is unset |
| `"transport":"direct"` | `CLICKHOUSE_TRANSPORT=direct` is set — remove it |
| `"reasoning":"Gemini on Vertex AI"` | requirement #2 satisfied |
| `"reasoning":"offline stub"` | no Gemini credentials reached the container |
| `"degraded":true` | at least one of the two above is still missing |

Record the video only once both lines read correctly. The header badge showing
`ClickHouse Cloud via MCP · Gemini on Vertex AI` is the single frame that proves
the track requirement to a judge.
