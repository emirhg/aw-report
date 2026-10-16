# Extra ActivityWatch Buckets: Findings (parked)

Status: **parked, not implemented.** The browser data is not reliable enough to mix into timeline
slots. Revisit after the browser collection problem is fixed at the source (see Open problems).

## Bucket inventory (ActivityWatch server, localhost:5600, v0.13.1)

All eight buckets on the server were listed, across all hostnames.

| Bucket | Type | Events today (2026-10-16 UTC) | All-time range | Notes |
|---|---|---|---|---|
| `aw-watcher-window_HerreraMonroy` | currentwindow | 948 | Apr-Oct | Used by tw-report today |
| `aw-watcher-afk_HerreraMonroy` | afkstatus | 5 | Apr-Oct | Used by tw-report today |
| `aw-watcher-taskwarrior_HerreraMonroy` | task-activity | 15 | Apr-Oct | Used by tw-report today |
| `aw-watcher-web-brave_HerreraMonroy` | web.tab.current | 1449 | Apr-Oct | Browser, active; hostname matches the machine |
| `aw-watcher-web-firefox` | web.tab.current | 0 | 2026-04-09 to 2026-07-29 (117 events) | Hostname `unknown` |
| `aw-watcher-web-brave` | web.tab.current | 0 | 2026-10-10 (1 event) | Hostname `unknown` |
| `aw-watcher-vim_HerreraMonroy` | app.editor.activity | 0 | 2026-04-03 to 2026-10-14 (3008 events) | Last event 2026-10-14 |
| `aw-watcher-tmux` | tmux.sessions | 0 | 2026-04-05 to 2026-10-15 (23425 events) | No hostname suffix; last event 2026-10-15 |

Data shapes:
- Browser: `url`, `title`, `audible`, `incognito`, `tabCount`.
- Vim: `file`, `language`, `project`. Durations are real (about 30-70 s).
- Tmux: `title`, `session_name`, `window_name`, `pane_title`, `pane_current_command`, `pane_current_path`.

## Browser bucket: why the data is not trustworthy

Measured on `aw-watcher-web-brave_HerreraMonroy`, today:

- 1581 events total, of which **1369 (87%) have duration 0**. Only 212 carry a real duration.
- `tabCount` changes between 7 and 52 within one day. That points to several browser windows or profiles
  writing into the same bucket.
- Overlap check on consecutive events with real durations: at least one pair overlaps. For example,
  `roadmap.sh/python` overlaps a YouTube URL. The check only compared neighbouring spans, so the true count
  is at least this and may be higher.

Reported by the user (not checked against the data here): concurrent windows send updates at the same time,
so the tab recorded as "current" is not always the focused tab. This is a race condition in how the browser
extension reports, not in tw-report.

Consequence: a browser event does not reliably say what the user was looking at. Attaching browser time to
timeline slots would present wrong attribution as fact.

## Decision

- Do not add browser, vim or tmux data to slot boundaries, slot detail or metrics.
- The `docs/OPTIMIZATION_OPPORTUNITIES.md` lazy-fetch note still applies to these buckets if they are added later.
- The discovery approach (match buckets by `type`, not by hostname or name) is still correct and is recorded here.

## Open problems (for further work)

1. **Browser race condition.** Find out why several windows report to one bucket and whether the extension can
   report only the focused window. The fix belongs in the browser watcher or its config, not in tw-report.
2. **Zero-duration heartbeats.** Most browser events have no duration, so spans must be inferred. Any inference
   (for example "until the next event, capped at 60 s") is only as good as the first point.
3. **Hostname-less buckets.** `aw-watcher-web-firefox`, `aw-watcher-web-brave` and `aw-watcher-tmux` have an
   empty or `unknown` hostname. Confirm which machine they came from before using them.
4. **Vim and tmux.** These have real durations and may be more reliable than the browser. Check for overlaps and
   whether they match the window bucket before using them.
5. **Overlap policy.** If a later design merges buckets, decide the rule for overlaps (for example, the window
   bucket wins for focus, and the others add detail only where they agree).

## How this was checked

- Server queries were read-only GET requests to `http://localhost:5600/api/0/...`.
- Counts were taken with `start=2026-10-16T00:00:00Z`, `end=2026-10-17T00:00:00Z`, `limit=-1`.
- Nothing in `src/` or `tests/` was changed.
