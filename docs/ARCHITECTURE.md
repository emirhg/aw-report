# tw-report architecture

This describes the code as it is on `refactor/robust-core`. The goal and the rules are in
`CLAUDE.md`; the file-by-file map is in `index.txt`.

## 1. Data flow

```
ActivityWatch buckets (afk, window)   TaskWarrior (aw-watcher-taskwarrior bucket)
            |                                   |
            +-------- core/events.py  (get_events: the only read path)
                              |
          core/aw_events.py  (typed events: AFKEvent, WindowEvent, TaskWarriorEvent)
                              |
          core/timeslot_builder.py  (sweep-line: non-overlapping ReportTimelineSlot list)
                              (each slot keeps the raw events overlapping its span in `events`,
                               looked up through core/event_index.py EventIndex)
                              |
          pipeline/consolidation.py  (only with --consolidate: merge same project/task)
                              |
          core/filtering.py  (EventFilter: applied to slots BEFORE metrics)
                              |
          pipeline/processors.py  (metrics, hierarchy, productivity split)
                              |
          pipeline/timeline_render.py   or   pipeline/report_render.py   (stdout only)
```

`cli/main.py::_run` is the only place that wires these stages together.

## 2. Layers

Dependencies point downward only: `cli` -> `pipeline` -> `core` -> `utils`. `core` holds the
domain rules and must not print or read the environment, except for the bucket and config lookups
that define where data comes from.

| Layer | Responsibility | Modules |
|---|---|---|
| cli | arguments, orchestration, exit codes | `cli/args.py`, `cli/main.py` |
| config | XDG config file, settings precedence, bucket roles | `config.py`, `core/buckets.py` |
| sources | reading ActivityWatch and TaskWarrior | `core/events.py`, `core/aw_events.py`, `core/task_*filtering.py`, `core/project_filtering.py`, `core/task_matching.py` |
| classification | categories and productivity scores | `core/categories.py` |
| filtering | sentinels and user filters | `core/filtering.py` |
| build | slot construction | `core/timeslot_builder.py` |
| model | slot and period types | `core/report_slot.py`, `core/timeline.py`, `core/period.py` |
| merge | session consolidation | `pipeline/consolidation.py` |
| metrics | totals, hierarchy, productivity | `pipeline/processors.py`, `pipeline/models.py` |
| render | text output | `pipeline/timeline_render.py`, `pipeline/report_render.py`, `pipeline/presenters.py` |
| utils | formatting, profiling, deprecation | `utils/*.py` |

## 3. Key decisions

- **One slot builder, two views.** The sweep-line builder produces slots once. The timeline and the
  hierarchical view both read those slots, so their totals agree by construction.
- **Filter before metrics.** `EventFilter` runs on the slots first; every total and percentage is
  computed from the surviving slots. Filtering before *building* would misclassify time, so the
  builder always sees the full event set.
- **Consolidation is a separate, opt-in merge.** The builder keeps state transitions as separate
  slots (chronological detail). `consolidate_sessions` decides cohesion and runs only with
  `--consolidate`. This is where entry boundaries for OpenProject will be decided.
- **Untracked time is data.** `No project assigned` and `No task assigned` are real rows, not
  holes, so totals always add up to the tracked period.
- **Typed events at the edge.** Raw ActivityWatch dicts become `AFKEvent`, `WindowEvent` or
  `TaskWarriorEvent` at the fetch boundary. Core logic uses the typed classes.
- **Fixed bucket roles.** The builder needs exactly three sources (afk, window, tasks). The
  `[buckets]` config changes only where each role is read from.
- **Deprecate, do not delete.** Production-unused API carries `@deprecated` and warns when called
  (`tw_report.utils.deprecation`); removal is a separate decision.

## 4. Errors and degradation

- `exceptions.py` defines `TwReportError` and its subclasses (`ConfigParsingError`,
  `ActivityWatchConnectionError`, `CategoryValidationError`, `TimelineSlotValidationError`).
- An unreachable ActivityWatch server degrades to an empty event list with a logged warning. The
  report then says "No activity found" instead of crashing.
- A bad config file, or a bad `[buckets]` table, prints one line on stderr and exits with code 1.
- `TimelineSlot.__post_init__` rejects slots without an explicit actual duration, and slots whose
  end does not match start plus duration. Invalid slots never reach the rest of the pipeline.

## 5. Configuration

Settings precedence: CLI arguments > config file > defaults. Buckets are resolved once in
`main()` and passed to the fetch helpers as IDs. See `CLAUDE.md` section 7 for the file format.

## 6. Testing strategy

- Per-module unit tests, with 100% line and branch coverage enforced in CI.
- Characterization tests drive `main()` end to end against deterministic fakes, and compare the
  output with checked-in golden snapshots. They are the regression net for any refactor.
- Known defects are recorded as strict xfail tests, so fixing one is a visible, deliberate change.
- Tests pin `TZ`; the suite must give the same result in any timezone.

## 7. Known structural debt

- `core/report_slot.py` (~1900 lines) mixes the slot model, merging and bucketing. The deprecated
  `ReportEntries` consolidation family lives there.
- The slot model still has a dict form and an object form; the renderers convert between them.
- `cli/main.py::_run` is still one long function; its stages should become named functions.
- `pipeline/presenters.py` is a thin wrapper and can be removed.

See `CLAUDE.md` section 12 for the pending cleanups to confirm with the user.
