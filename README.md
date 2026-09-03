# The Control Room

An agent crew that finds out why a live stream is breaking, before the on-call engineer has finished reading the alert.

Built on Gemini and Google Cloud Agent Builder (ADK), against ClickHouse.

---

## The problem

During a live event, a quality alert says one thing: *rebuffering is up*. It does not say which CDN, which region, which device class, or which of the four things that changed in the last hour caused it. On-call engineers pivot through dashboards for 20 to 40 minutes while viewers leave. For a 2-million-concurrent broadcast, every minute of that is measurable churn and forgone ad revenue.

The data to answer it already exists — billions of player heartbeats. Nobody can query it fast enough under pressure.

## What this does

Six agents run in a fixed order, every time:

| | Agent | What it does |
|---|---|---|
| 1 | **Watcher** | z-score sweep over a per-minute rollup. Incident, or normal variance? |
| 2 | **Diagnostician** | Explanatory-power attribution across six dimensions, then drills into individual sessions in the culprit slice. |
| 3 | **Eyewitness** | Gemini *looks at frames* from the affected renditions. Clean picture with stalls means delivery. Macroblocking means encoder. This decides who gets paged. |
| 4 | **Impact** | Sessions hit, abandons, watch-time lost, revenue at risk. |
| 5 | **Action** | Remediation the engineer can execute in two minutes, plus the viewer-facing post. |
| 6 | **Continuity** | Re-measures the culprit slice. An incident closes because the numbers came back, not because an agent wrote a plan. |

Every step — including the exact SQL and rows scanned — is written back to ClickHouse, so any run can be replayed after the fact.

## The part that matters: it's deterministic

Gemini decides **what the evidence means**. It never decides **which evidence to collect** on the critical path.

Detection and diagnosis run SQL that ships in this repo (`sql/queries/`) with server-side bound parameters. Re-run a `run_id` against the same window and you get the same queries and the same culprit. That is the difference between a demo and something you would let touch a live broadcast.

The ClickHouse MCP server *is* attached — but only for open-ended follow-up ("was BLR1 affected too?"), where a wrong query costs nothing. See `controlroom/agents.py`.

## Architecture

```
player telemetry ──▶ ClickHouse
                       ├── playback_events   raw, MergeTree, 90-day TTL
                       ├── qoe_1m            AggregatingMergeTree rollup ◀── detection reads here
                       ├── agent_runs        every step, every query, replayable
                       └── incidents
                              ▲
                              │
  ADK root_agent ── open_control_room ──▶ pipeline (6 fixed steps)
   (Agent Engine)              │            ├── Gemini 2.5 Flash   watcher verdict
        │                      │            ├── Gemini 2.5 Pro     diagnosis, action
        └── ClickHouse MCP     │            └── Gemini 2.5 Pro     vision — frame inspection
            (follow-ups only)  │
                               ├── evals/run_eval.py  ground-truth accuracy
                               ▼
                    FastAPI + SSE ──▶ Control Room UI (Cloud Run)
```

## Google Cloud and ClickHouse, actually called

Not name-dropped in a README — imported and executed:

- `controlroom/pipeline.py` — `google.genai` calls to Gemini 2.5 Pro/Flash on Vertex AI, structured JSON responses.
- `controlroom/eyewitness.py` — Gemini multimodal, real image bytes passed as `types.Part.from_bytes`.
- `controlroom/agents.py` — ADK `Agent` + `McpToolset` running the official `mcp-clickhouse` server over stdio.
- `controlroom/ch.py` — `clickhouse-connect`, parameterised queries, run logging.
- Deployed on Cloud Run; agent deployable to Vertex AI Agent Engine via `adk deploy agent_engine`.

## Run it with no account, no keys, one command

```bash
git clone <this repo> && cd control-room
./scripts/demo.sh
```

Open http://localhost:8080 and press **Roll cameras**. The full six-agent run executes against an embedded ClickHouse (`chdb` — the same engine, compiled in-process) seeded with 250k events, and the reasoning steps fall back to rule-based stubs.

Both substitutions are labelled in the header bar, in `/api/mode`, and in the `model` column of every logged step. Nothing pretends to be Gemini that isn't. The point is that a reviewer can see the whole product work before deciding whether to plug in credentials.

## Run it for real

```bash
cp .env.example .env        # ClickHouse Cloud + GCP project
./scripts/bootstrap.sh      # schema, 5M events, server on :8080
```

Talk to the agent instead:

```bash
adk web controlroom          # local chat UI
adk deploy agent_engine --project $GOOGLE_CLOUD_PROJECT --region $GOOGLE_CLOUD_LOCATION controlroom
```

Deploy the dashboard:

```bash
gcloud run deploy control-room --source . --region $GOOGLE_CLOUD_LOCATION --allow-unauthenticated
```

## Does it actually work? Measure it.

Claiming an agent diagnoses incidents is easy. `evals/run_eval.py` scores it against ground truth on six fault archetypes, alongside three baselines that represent what dashboards and naive LLM prompts actually do. It runs on an embedded ClickHouse — no cloud account, no credentials:

```bash
pip install chdb numpy
python evals/run_eval.py --trials 3
```

```
strategy                   top-1       top-2
worst_qoe              0/18   0.0%    0/18   0.0%
largest_total          0/18   0.0%    0/18   0.0%
biggest_delta          4/18  22.2%    5/18  27.8%
control_room          16/18  88.9%   17/18  94.4%  █████████████████

diagnosis latency: median 488 ms over 6 dimensions
```

Every dataset contains a chronic-but-innocent slice: BSNL viewers pinned to the bottom of the ABR ladder, who stall constantly and always have. They are the worst-looking rows in the table and they are never the answer. Two of the three baselines blame them on **all 18 runs** — which is exactly the 2am mistake this system exists to prevent.

### Where it fails

Both misses are the same failure, and it is worth stating plainly: the ranking named the cohort that suffered most (`device_type=mobile_ios`, `device_type=smart_tv`) instead of the fault that caused it. In both cases the correct slice came second and went into the page as the secondary, which is why top-2 is reported alongside top-1 — the on-call engineer received the right information, ranked wrong.

The tiebreak weight in `controlroom/attribution.py` was tuned on one seed. The 88.9% above is measured on two seeds it was not tuned on.

### Why explanatory power

Severity does not identify a root cause. Containment does.

If a bad player release is the fault, then essentially *all* the excess stall time lives inside that version — explanatory power near 1. The device class that suffers most is only a slice of the affected population, so it explains a fraction. Ranking on "how much of the fault does this slice contain" separates the cause from the cohort that merely hurts.

When two slices are both largely contained — a CDN fault and the smart TVs it hits hardest — Jensen-Shannon divergence between baseline and incident share breaks the tie. The SQL is in `sql/queries/blame.sql`, roughly 40 lines, and it runs in half a second.

One detail that matters: `controlroom/attribution.py` holds the scoring formula, and both the pipeline and the eval import it. If the eval scored one formula while production ran another, the number above would be fiction.

### And the SQL itself

```bash
python tests/test_sql.py
```

Creates the schema, fires the materialized view, inserts 300k events with one planted fault, and asserts the ranking finds it:

```
cdn              → edgecast         EP=1.318 surprise=0.00470
device_type      → smart_tv         EP=0.651 surprise=0.00023
rendition        → 360p             EP=0.745 surprise=0.00001
culprit: cdn=edgecast
```

## The dataset

`data/generate_events.py` produces realistic player telemetry — ABR ladder walks, buffer health, error codes, ad completions — and injects one of six fault archetypes:

```bash
python data/generate_events.py --list
```

| scenario | fault | who should be paged |
|---|---|---|
| `shield_eviction` | origin shield evicting segments in one POP | delivery |
| `isp_peering` | peering congestion mid-event | delivery |
| `encoder_corruption` | corrupt slices on the top rendition | encoder |
| `packager_manifest` | stale manifest published | packager |
| `player_regression` | player release regressing buffer management | client |
| `regional_pop` | one edge POP degraded, all CDNs fine | delivery |

Faults are deliberately subtle — a few hundred milliseconds on a minority of sessions — and every dataset carries the chronic decoy described above. Nothing about the fault is passed to the agents. They read it out of the data.

## Layout

```
sql/01_schema.sql        tables, rollup MV, run log
sql/queries/             the only SQL on the critical path
data/scenarios.py        six fault archetypes + the confounder
data/generate_events.py  telemetry generator
controlroom/pipeline.py  the six fixed steps
controlroom/attribution.py  the ranking formula, shared by pipeline and eval
controlroom/llm.py       Gemini, plus labelled offline fallbacks
controlroom/agents.py    ADK agent + ClickHouse MCP
controlroom/eyewitness.py  Gemini vision on stream frames
controlroom/server.py    FastAPI, SSE
web/index.html           the control room
evals/run_eval.py        accuracy vs ground truth, against three baselines
tests/test_sql.py        SQL verified against a real ClickHouse engine
scripts/demo.sh          zero-credential run
```

## License

MIT. See [LICENSE](LICENSE).
