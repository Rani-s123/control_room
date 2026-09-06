# Agentic Cinema — submission checklist

**Track:** ClickHouse · **Deadline:** 9 Sep 2026, 2:00 PM PDT = **10 Sep, 2:30 AM IST**
**Repo:** github.com/Rani-s123/control_room · **Prizes:** $7,500 / $4,500 / $3,000

---

## Hard requirements

| # | Rule | Status | Action |
|---|---|---|---|
| 1 | "must actively use ClickHouse at runtime via the official ClickHouse MCP server (mcp-clickhouse), connecting to a ClickHouse Cloud or self-hosted cluster" | **Code done, not proven live** | Deploy against ClickHouse Cloud and confirm `/healthz` returns `"transport":"mcp"` |
| 2 | Built using Gemini + Google Cloud | Code done, not proven live | Set Vertex env vars; confirm `/api/mode` shows `Gemini on Vertex AI`, not `offline stub` |
| 3 | Services "imported and actually called… not just named in the README" | Satisfied once #1 and #2 are live | — |
| 4 | Hosted project URL | **Missing** | `gcloud run deploy` (below) |
| 5 | Public repo, OSS license at top | Repo public, MIT present | **Push the local commit** |
| 6 | Demo video ≤ 3 min, public, English/subtitled | **Missing** | Script in `VIDEO-SCRIPT.md` |
| 7 | Runs on web / Android / iOS | Web ✓ | — |
| 8 | New project, built 27 Jul – 9 Sep 2026 | First commit 3 Sep ✓ | — |
| 9 | Text description: features, tech, data sources, learnings | **Missing** | Draft on Devpost form |
| 10 | Team ≤ 4, all added on Devpost | — | Confirm |

**Rule to read yourself:** *"Projects may only use Google Cloud artificial intelligence tools… No other AI models, agent frameworks, or AI APIs are permitted."* The natural reading is the project's **runtime** (yours is Gemini-only, so fine) rather than tools used while building — the IBM track mandates a build-time AI assistant, which supports that reading. But the stakes are disqualification, so read it once and decide for yourself.

---

## Order of work

### 1. Push (5 min) — do this first
Your Windows terminal, not the Cowork shell (that one has no credentials):

```
cd D:\control-room
git log --oneline -1        # should show: Run every critical-path read through...
git push origin main
```

GitHub still shows 2 commits and the old 88.9% README until you do.

### 2. ClickHouse Cloud (20 min)
Redeem the **$400 hackathon credits**, create a service, then from `D:\control-room`:

```
copy .env.example .env
# fill CLICKHOUSE_HOST / USER / PASSWORD, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION
bash scripts/bootstrap.sh     # schema + 5M events
curl -s localhost:8080/healthz
```

**Gate:** the response must read `"transport":"mcp"`. If it says `embedded`, `.env` did not load. If it says `direct`, unset `CLICKHOUSE_TRANSPORT`. Do not proceed until this says `mcp` — it is requirement #1.

### 3. Vertex AI (15 min)
```
gcloud services enable aiplatform.googleapis.com
gcloud auth application-default login
curl -s localhost:8080/api/mode
```
Must show `"reasoning":"Gemini on Vertex AI"`. Then run once and confirm the timeline's `model` column reads `gemini-2.5-pro` / `gemini-2.5-flash`, not `offline-stub`.

### 4. Cloud Run (30 min)
```
gcloud secrets create clickhouse-password --data-file=-
gcloud run deploy control-room --source . ^
  --region %GOOGLE_CLOUD_LOCATION% --allow-unauthenticated ^
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=%GOOGLE_CLOUD_PROJECT%,GOOGLE_CLOUD_LOCATION=%GOOGLE_CLOUD_LOCATION%,CLICKHOUSE_HOST=%CLICKHOUSE_HOST%,CLICKHOUSE_USER=%CLICKHOUSE_USER%,CLICKHOUSE_SECURE=true,CLICKHOUSE_DATABASE=control_room" ^
  --set-secrets "CLICKHOUSE_PASSWORD=clickhouse-password:latest"
```
Grant the runtime service account `roles/aiplatform.user`. Then hit the public URL's `/healthz` and `/api/mode` from a browser — judges will.

Set `--timeout 300` and `--memory 2Gi`; a full run streams for well over the 60s default.

### 5. Record the video (60 min) — see `VIDEO-SCRIPT.md`

### 6. Devpost form (30 min)
Select the **ClickHouse** track explicitly. In the description, name the MCP server in the first three lines — a judge scanning for the track requirement should not have to hunt.

---

## What to say, and what not to

**Lead with these — they are unusual for a hackathon and all verified:**

- Accuracy reported on **held-out seeds**: 93.9% top-1 / 98.5% top-2 over 66 datasets; 93.8% on the 48 seeds the tiebreak weight was never fitted on. The fitted-vs-held-out gap is 0.6 points, and both columns are printed.
- Detection measured **separately** from attribution, because step 1 is a gate: 17/18.
- A **fault-severity curve** down to the noise floor, so the headline number has a context.
- Baselines that fail the way real dashboards fail: two of three blame the chronic-but-innocent BSNL cohort on all 66 runs.
- Every step, including the SQL and rows scanned, written back to ClickHouse and replayable by `run_id`.

**Say the weak parts before a judge finds them.** `regional_pop` is 8/11 top-1 and misses detection about one run in three, because `region=DE-BE` is the smallest slice any archetype plants a fault in. This is in the README already. Volunteering it reads as engineering judgment; being caught on it reads as the opposite.

**Do not** claim the demo proves Gemini or ClickHouse Cloud. The zero-credential mode is labelled `offline stub` and `embedded (chdb)` on purpose — that labelling is a strength, but the *hosted* URL has to be the real thing.

---

## Honest read on the odds

9,651 registered across 5 tracks — though registrations are far larger than submissions, and the ClickHouse track takes a slice of that. Three prizes.

Judging is four **equal-weighted** criteria. Quality of the Idea is your strongest: explanatory-power attribution plus a planted decoy that defeats the naive panels is genuinely non-obvious, and most entries will be a chat wrapper over a database. Technological Implementation is strong now that MCP is on the critical path. Potential Impact is specific and quantified. Design is good — complete UI, six-step timeline, scenario switcher.

With #1–#6 closed, this is a credible top-3 contender in the ClickHouse track. Without the deployed MCP proof, quality will not save it, because a judge checking the track requirement gets no evidence.

The single highest-value hour you can spend is step 2 — getting `/healthz` to say `mcp` against ClickHouse Cloud. Everything else is presentation.
