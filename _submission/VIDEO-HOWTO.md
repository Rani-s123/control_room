# How to actually record the 3-minute video

VIDEO-SCRIPT.md has the words and the timing. This is the mechanics: what to
open, what to press, and the order that means you never have to re-record the
whole thing.

**Live URL:** https://controlroom-production-f82a.up.railway.app

---

## 1. Before you press record (15 min, and it saves an hour)

**Warm the app up.** Open the URL, run one full incident. A cold container and
an idle ClickHouse service both take a few seconds the first time; you do not
want that on camera. Throw this run away.

**Check the header says the right thing.** It must read
`ClickHouse Cloud via MCP · Gemini`. If it says `embedded (chdb)` or
`offline stub`, stop and fix that first - recording the demo mode undercuts the
entire submission.

**Open exactly these tabs, in this order, and nothing else:**

1. The app - https://controlroom-production-f82a.up.railway.app
2. `/healthz` - https://controlroom-production-f82a.up.railway.app/healthz
3. The repo - https://github.com/Rani-s123/control_room

**Silence the machine.** Windows Settings > System > Notifications > off.
Close Slack, WhatsApp, mail. One toast notification mid-take costs a re-record.

**Clean the browser.** Full screen (F11). Hide the bookmarks bar (Ctrl+Shift+B).
Browser zoom at 100% (Ctrl+0). No other tabs visible.

**Have the eval numbers ready.** See section 5 - this is the one segment that
can go wrong, so decide your approach before you start.

---

## 2. Recording tool

**Windows Game Bar** is already installed and is enough:
- `Win + G` opens it, `Win + Alt + R` starts and stops recording
- Records the active window with microphone if you enable the mic icon
- Files land in `Videos\Captures`

**OBS Studio** (free, obsproject.com) if you want more control - set
Display Capture, 1920x1080, 30fps.

Either is fine. Do not record on a phone pointed at the screen.

---

## 3. Record in five separate takes, not one

One continuous 3-minute take means any mistake costs you the whole thing.
Record each section separately and join them at the end. Nobody can tell.

| Take | Script section | Screen |
|---|---|---|
| 1 | 0:00 - 0:38 | Dashboard idle, then the header badge |
| 2 | 0:38 - 1:40 | Pick `encoder_corruption`, Roll Cameras, let it stream |
| 3 | 1:40 - 2:05 | Eyewitness frames, Impact card, Action card, Continuity |
| 4 | 2:05 - 2:35 | The eval numbers (section 5 below) |
| 5 | 2:35 - 2:52 | `/healthz` tab, then the repo |

Take 2 is the long one. Record it at normal speed and cut the dead air between
agent steps afterwards - do not speed up the video itself, the timeline text has
to stay readable.

---

## 4. Narration

Two options, both fine:

**Live** - read the script while recording. Fastest. Read slower than feels
natural; the script has about 8 seconds of headroom.

**Separate** - record the screen silent, then record your voice over it in the
editor. Easier to fix a fumbled line, and usually sounds calmer.

Either way: read it out loud twice before recording. The eval section (2:05)
is your strongest 30 seconds and the easiest to rush.

---

## 5. The eval segment - decide this in advance

The script asks for `python evals/run_eval.py --trials 11` on screen. That is
66 full runs. It can take a long time, and if the dependencies are not installed
locally it will not run at all.

**Option A - run it ahead of time.** Start it now, in a terminal you leave open.
When it finishes, record the finished output. Best looking option.

```
cd D:\control-room
python evals/run_eval.py --trials 11
```

**Option B - show the README table instead.** Scroll to the accuracy section of
the README on GitHub and hold on the numbers while you narrate. Completely
legitimate, takes 20 seconds, zero risk. Say "we measured it, here are the
numbers, the harness is in the repo" and move on.

Do not skip this segment either way. Measured accuracy against baselines is the
strongest thing in the whole submission, and most entries will have nothing
like it.

---

## 6. Editing

**Clipchamp** ships with Windows 11 and is enough: drag the five takes onto the
timeline in order, cut the dead air, export 1080p.

Check the total is **under 3:00**. The rules say 3 minutes; over that risks
disqualification. If you are 10 seconds over, cut from the problem statement at
the start, never from the eval section.

---

## 7. Upload

- YouTube, **Public** - not Unlisted, not Private
- Title: `The Control Room - agentic incident response on ClickHouse and Gemini`
- Turn on auto-captions, then **review them**. "Explanatory power", "rebuffering"
  and "ClickHouse" all get mangled, and the rules require English or English
  subtitles.
- Open the link in an incognito window before you paste it into Devpost. A video
  a judge cannot play is a video that scores zero.

---

## 8. The two shots that matter most

If everything else goes wrong, these two frames still prove the track requirement:

1. **The header badge** reading `ClickHouse Cloud via MCP · Gemini` - hold two seconds
2. **`/healthz`** showing `{"status":"ok","events_loaded":1000000,"transport":"mcp"}`

A judge checking whether you actually used ClickHouse at runtime is looking for
exactly these. Four seconds of screen time, and they decide the track.
