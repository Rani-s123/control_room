# Devpost form - field by field

Open https://agentic-cinema.devpost.com and click **Submit a project** (or
Manage your submission if a draft already exists). Fill each field with the
block below it. Nothing here needs rewriting.

---

## Project name

```
The Control Room
```

## Elevator pitch  (Devpost caps this at 200 characters)

```
Six agents that find why a live stream is breaking. Every read runs through the official ClickHouse MCP server against ClickHouse Cloud. 93.9% top-1 root-cause accuracy over 66 datasets.
```

## Project details  (the long rich-text box)

Paste the whole of `_submission/DEVPOST-DESCRIPTION.md`, starting at
**"## Inspiration"** and going to the end. Skip the title line and the
"Track: ClickHouse" note at the top - those are instructions to you, not
content for the form.

Devpost's editor accepts markdown headings, so the sections land as headings
automatically.

## Built with  (tag field - type each, press Enter)

```
gemini
vertex-ai
google-adk
google-cloud
clickhouse
clickhouse-cloud
mcp
mcp-clickhouse
model-context-protocol
python
fastapi
server-sent-events
chdb
docker
```

## Try it out links

```
https://controlroom-production-f82a.up.railway.app
https://github.com/Rani-s123/control_room
```

## Video demo link

```
<your YouTube URL - must be Public, not Unlisted>
```

## Partner track

Select **ClickHouse**. This is the single most important dropdown on the page -
picking the wrong track puts the project in a category it cannot win.

## Image / thumbnail

Upload `web/banner.png` from the repo. Devpost shows this on the gallery card,
and a submission with no image reads as unfinished.

## Team

Add every teammate as a Devpost collaborator. The rules require it - a person
not listed on the submission is not on the team as far as judging is concerned.

---

## Before you hit Submit

| Check | Why |
|---|---|
| Video plays in an incognito window | A video judges cannot open scores zero |
| Video is under 3:00 | Over the limit risks disqualification |
| Video has English subtitles | Required by the rules |
| Hosted URL opens and the header reads `ClickHouse Cloud via MCP · Gemini` | This is requirement #1 |
| `/healthz` returns `"transport":"mcp"` | The cheapest proof of the same thing |
| Repo is public and the MIT license shows on the repo page | Required, and it is |
| ClickHouse track selected | Wrong track = wrong competition |

## Two links a judge will open first

```
https://controlroom-production-f82a.up.railway.app/healthz
https://controlroom-production-f82a.up.railway.app/api/mode
```

Both currently return the right thing. Worth pasting them into the project
details near the top so a judge checking the track requirement does not have to
hunt for them.
