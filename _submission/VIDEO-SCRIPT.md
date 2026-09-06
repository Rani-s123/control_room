# 3-minute demo video — script

Hard limit 3:00. This runs ~2:52 read at a normal pace. Record the screen against the **deployed Cloud Run URL** with real credentials, never the offline demo — the judging criterion is how well the project uses Google Cloud and ClickHouse, so both have to be visibly live.

Judges score four equal-weighted criteria. Each section below is aimed at one, in the order that keeps a viewer watching.

---

## 0:00 – 0:22 · The problem
**Screen:** the dashboard, idle. Then hover the Watcher card.

> During a live final, one alert fires: rebuffering is up. It doesn't say which CDN, which region, which device, or which of the four things that changed in the last hour caused it. On-call engineers pivot through dashboards for twenty to forty minutes while viewers leave. The data to answer it already exists — billions of player heartbeats. Nobody can query it fast enough under pressure.

## 0:22 – 0:38 · What it is, and the one thing to notice
**Screen:** header badge reading `ClickHouse Cloud via MCP · Gemini on Vertex AI`. Hold for two seconds — this is the frame that proves the track requirement.

> The Control Room is six agents that run in a fixed order every time. Every read they make is executed by the official ClickHouse MCP server against ClickHouse Cloud, and every one of those queries ships in the repo. Gemini decides what the evidence means. It never decides which evidence to collect.

## 0:38 – 1:18 · A run, end to end
**Screen:** pick `encoder_corruption`, click Roll Cameras. Let the timeline stream. Do not narrate every step — let two land on their own.

> Watcher first. The all-up average is inside normal variance — a global QoE panel would call this quiet and stop. So it repeats the sweep per slice, against each slice's own baseline, and finds one rendition running three sigma above its own normal for the whole window. That is the blind spot this system exists to replace, and it was in our own step one until we measured it.

**Screen:** expand the Diagnostician's ranked candidate table.

> Diagnostician ranks by explanatory power — what share of the unforecast stall time each slice actually contains. Severity doesn't identify a root cause. Containment does.

## 1:18 – 1:40 · The decoy
**Screen:** point at `isp=BSNL` sitting near the top on raw rebuffering and low on the ranking.

> Every dataset carries a trap: BSNL viewers pinned to the bottom of the ladder, who stall constantly and always have. Worst-looking rows in the table, never the answer. Two of the three baseline strategies blame them on all sixty-six test runs. That's the two a.m. mistake this exists to prevent.

## 1:40 – 2:05 · Vision, impact, action
**Screen:** Eyewitness frames, then the Impact and Action cards.

> Metrics say viewers are stalling. They can't say whether the picture those viewers received was intact. Gemini looks at frames from the affected rendition — macroblocking means encoder, a clean picture means delivery. That single call changes which team gets paged. Then blast radius, revenue at risk, and a remediation the engineer can execute in two minutes.

**Screen:** Continuity card.

> And the incident closes because the numbers came back, not because an agent wrote a plan.

## 2:05 – 2:35 · Does it actually work
**Screen:** terminal, `python evals/run_eval.py --trials 11` output already on screen. Highlight the fitted / held-out columns.

> Claiming an agent diagnoses incidents is easy, so we measured it. Sixty-six datasets, six fault archetypes. Ninety-four percent top-one — and ninety-four again on the forty-eight seeds the tiebreak weight was never fitted on, which is the number that counts. Detection is measured separately, because step one is a gate. The naive dashboard strategies score zero.

**Screen:** scroll to the per-scenario table.

> One archetype is weak. A regional POP fault lands in the smallest slice in the dataset, and we miss it about a third of the time. It's in the README.

## 2:35 – 2:52 · Close
**Screen:** `/healthz` in a browser tab showing `"transport":"mcp"`, then the repo.

> Every step, including the exact SQL and rows scanned, is written back to ClickHouse, so any run replays by ID. Deterministic where it has to be, and honest about where it isn't.

---

## Recording notes

- **Do a dry run first.** A cold Cloud Run instance loads slowly; hit the URL once before recording.
- **Speed up the streaming.** Record at normal speed, then cut the dead air between steps in post. Do not speed up the terminal — judges read it.
- **Show `/healthz` on screen at least once.** It is the cheapest possible proof of requirement #1 and takes four seconds.
- **Do not show the offline demo.** `offline stub` and `embedded (chdb)` in the header undercuts the whole submission on video, even though the honesty is a strength in the repo.
- **Subtitles.** Rules require English or English subtitles. YouTube auto-captions are usually acceptable but review them — "explanatory power" and "rebuffering" get mangled.
- **Set the video public**, not unlisted-and-forgotten. Check it plays in an incognito window before you paste the link.
- Read slightly slower than feels natural. The script has ~8 seconds of headroom; use it rather than rushing the eval section, which is your strongest 30 seconds.
