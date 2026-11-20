# CLAUDE.md - tw-report

Read this file before changing anything. It states the goal, where things live, the rules that
must not be broken, and how to verify a change. Per-module detail is in `index.txt`; the design
is in `docs/ARCHITECTURE.md`; user-facing behaviour is in `README.md`.

## 1. Goal

tw-report turns ActivityWatch activity (AFK, active window) and TaskWarrior tasks into timesheet
data. The end goal is **time entries pushed to OpenProject**. The push format is not defined yet,
so the work now is the part that comes first, in priority order:

1. **Classify every event** (window, AFK, TaskWarrior) into a project and task. Unassigned time
   stays visible under the sentinels `No project assigned` / `No task assigned`; it is never
   silently dropped.
2. **Build continuous entries as large as possible while preserving cohesion.** One entry is one
   coherent piece of work. Merge what belongs together (same project and task across short
   interruptions). Do not merge across other tasks, across real breaks, or across days unless
   that is a deliberate, documented choice.
3. Only then render or export (text today, OpenProject later).

Current status of the merge rule: merging happens only with `--consolidate`
(`pipeline/consolidation.py::consolidate_sessions`). Without the flag, every builder slot is shown
as-is. With the flag, same project and task joins across any gap and is broken only by a different
task; AFK slots carry no task, so an AFK break splits an entry. Whether short AFK breaks should be
bridged, and whether merging may cross midnight, are open decisions. Do not change them without
asking.

## 2. Quick start

```bash
# Python 3.14 only (requires-python >=3.14). The project venv is .venv in the repo.
python3.14 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/aw-report :yesterday          # run against a local ActivityWatch server
```

A `pip install -e` from another checkout (for example the main checkout) points `tw_report` at
that checkout. Always run the tests from the venv of the tree you are editing.

## 3. Verification gates (all must pass before a commit)

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider --cov --cov-branch   # fail_under = 100 (line + branch)
.venv/bin/ruff check src && .venv/bin/ruff format --check src
.venv/bin/mypy src
```

- Coverage is enforced at **100% line and branch** and there are **no pragmas** in `src`.
  Unreachable code is deleted or deprecated, never excluded.
- Tests pin `TZ` themselves; results must be identical under `TZ=UTC`, `America/New_York` and
  `Asia/Tokyo`. Logical dates use local time, so any new time-sensitive test must pin `TZ`.
- `pytest` runs with `filterwarnings = error`. Only the four deprecation messages listed in
  `pyproject.toml` are ignored; do not widen that list without a reason.
- **Golden snapshots** (`tests/characterization/golden/*.txt`) pin the CLI output. They must not
  change unless the output change is intended. Regenerate only with
  `pytest tests/characterization --update-golden`, then review the diff.

## 4. Map of the code

Layers depend only downward: `cli` -> `pipeline` -> `core` -> `utils`. Keep it that way.

| Layer | Module | Responsibility |
|---|---|---|
| cli | `cli/args.py` | argparse flags; reorders positional terms |
| cli | `cli/main.py` | orchestration: fetch, filter, build, metrics, render (`main()` -> `_run()`) |
| config | `config.py` | XDG config file (`$XDG_CONFIG_HOME/tw-report/config.toml`), settings precedence |
| config | `core/buckets.py` | resolves the `[buckets]` table into the afk / window / tasks bucket IDs |
| sources | `core/events.py` | `get_bucket_id`, `get_events` (the only ActivityWatch read path) |
| sources | `core/aw_events.py` | typed events: `WindowEvent`, `AFKEvent`, `TaskWarriorEvent` |
| sources | `core/task_uuid_filtering.py`, `core/task_filtering.py`, `core/project_filtering.py`, `core/task_matching.py` | TaskWarrior lookups by ID/UUID/name and offline-tag checks |
| classify | `core/categories.py` | ActivityWatch category rules (regex) and productivity scores |
| filter | `core/filtering.py` | `EventFilter` (sentinels, `--project`, `--task`, `--app`, exclusions); `NO_PROJECT`, `NO_TASK` |
| build | `core/timeslot_builder.py` | sweep-line builder: non-overlapping slots from all three sources |
| model | `core/report_slot.py` | `ReportTimelineSlot` (the slot model), `ReportEntries` (mostly deprecated) |
| model | `core/timeline.py` | `TimelineSlot` (validated slot dataclass used by the builder) |
| model | `core/period.py` | period tokens (`:today`, `:yesterday`, `:week`, `:lastweek`, `:month`, `:year`, `:all`) and explicit dates (`2026-08-30`, or `start end`) |
| merge | `pipeline/consolidation.py` | `consolidate_sessions` (only with `--consolidate`) |
| metrics | `pipeline/processors.py` | `compute_metrics`, `aggregate_hierarchy_from_slots`, `compute_productivity_breakdown`, AFK merge |
| metrics | `pipeline/models.py` | `TimeslotDuration`, `PeriodMetrics`, `ReportTotals`, `ReportContext` |
| render | `pipeline/timeline_render.py` | timeline view (`print_timeline_report`) |
| render | `pipeline/report_render.py` | hierarchical view (`print_report`, summary and totals printers) |
| render | `pipeline/timesheet_render.py` | text timesheet (`--format timesheet`): per-period check-in/out, hours and tasks from raw taskwarrior events, clipped to each period; layout as `samples/timesheet_report.py` |
| render | `pipeline/presenters.py` | thin wrappers around the two renderers (candidate for removal) |
| utils | `utils/formatting.py` | durations, titles, column layout, `to_local_time` |
| utils | `utils/profiling.py` | `TW_REPORT_PROFILE` stage timing (opt-in) |
| utils | `utils/deprecation.py` | the `deprecated` decorator (stdlib `warnings.deprecated`) |
| errors | `exceptions.py` | `TwReportError` hierarchy (`ConfigParsingError`, `ActivityWatchConnectionError`, ...) |

Module-level descriptions with line counts are in `index.txt`.

## 5. Pipeline (what `main()` does, in order)

1. Parse arguments; load the config and resolve the buckets and settings. A bad config prints one
   line on stderr and exits 1.
2. Parse the period and the grouping mode (`--by-*`, `--consolidate`, `--detail-level`).
3. Resolve task selectors: `--task-id`, numeric or UUID `--task`, `--project` and `--task` values.
4. Pre-fetch TaskWarrior events for the selected tasks (typed fetch) and derive the time ranges.
5. Fetch AFK and window events, restricted to those ranges (window events only when needed).
   `--app` narrows the ranges to the app's usage envelope.
6. Deduplicate TaskWarrior heartbeats (typed fetches only, see defect list), then build slots with
   `build_timeslot_timeline`.
7. Convert to dicts, apply `consolidate_sessions` if `--consolidate`.
8. **Filter** the slots with `EventFilter`. This happens **before** any metric is computed.
9. Compute metrics from the filtered slots (`compute_afk_offline_totals`, `compute_metrics`,
   `compute_productivity_breakdown`).
10. Render with `print_timeline_report` (default) or `print_report` (`--by-project`). The
    hierarchical view is built from the same filtered slots, so both views agree.

## 6. Invariants (do not break)

- **Single pipeline.** Slots are built once and feed both views. No second slot-building path.
- **Filter before metrics.** Totals and percentages come from filtered slots, never from
  unfiltered context values.
- **The builder sees unfiltered events.** Filtering before building misclassifies time.
- **Explicit actual duration.** `TimelineSlot.actual_duration` is required and never inferred.
- **Untracked is explicit.** Use `NO_PROJECT` / `NO_TASK` (`core/filtering.py`); normalise every
  spelling through `normalize_project` / `normalize_task`. Do not add new spellings.
- **Offline tasks** are slots whose task has the `offline` tag. Offline time comes from the event
  duration, not from window coverage.
- **Bucket IDs come from `core/buckets.py`** (which uses `core/events.py::get_bucket_id`). Do not
  build `aw-watcher-*` strings elsewhere.
- **Sources are read only through `core/events.py::get_events`** (it degrades to an empty list
  with a warning when ActivityWatch is unreachable).

Time metrics (domain definitions, keep them consistent in code and output):
- **Online time**: AFK bucket total, AFK plus not-AFK.
- **Active time**: not-AFK time only (online minus AFK).
- **Offline time**: work done while the machine was off, for offline-tagged tasks only.
- `Total day = online + offline`.

## 7. Configuration

`$XDG_CONFIG_HOME/tw-report/config.toml` (fallback `~/.config/tw-report/config.toml`).

```toml
detail_level = 3
day_start_hour = 0          # 0 = local midnight (default)
exclude_projects = ["Personal"]
terminal_width = 120

[activitywatch]    # optional; host/port fall back to aw-client.toml, then 127.0.0.1:5600
host = "127.0.0.1"
port = 5600
timezone = "America/Mexico_City"   # optional; period boundaries (default: system zone)

[buckets]          # optional; defaults are aw-watcher-<watcher>_<hostname>
hostname = "laptop"
window = "aw-watcher-tmux"   # exact ID of one role
```

Only `hostname`, `afk`, `window` and `tasks` are accepted in `[buckets]`; anything else is an error.
Only `host`, `port` and `timezone` are accepted in `[activitywatch]`.
`aw-report fetch [PERIOD]` prints the events of the afk, window and tasks buckets as a JSON array
(three arrays, UTC timestamps).
The category rules come from ActivityWatch's `settings.json` (`--categories` overrides the path).

## 8. Deprecated API policy

Production-unused code is **deprecated, not deleted** (decision of the user). Each item carries
`@deprecated("...")` from `tw_report.utils.deprecation` and emits a `DeprecationWarning` when
called. The current list is in `tests/unit/test_deprecations.py::CASES`. Rules:

- New code must not call a deprecated API. Use its replacement named in the message.
- `task_based=False` in `print_report_summary` / `print_report` is the deprecated category layout
  and warns on use.
- Removal happens only when the user asks for it.

## 9. Known defects (strict xfail: they pin the bug until fixed)

| Test | Defect |
|---|---|
| `tests/unit/test_timeline_render_coverage.py::test_detail_level_one_hides_task_names` | `--detail-level 1` still prints task names |
| `tests/unit/test_timeline_render_coverage.py::test_offline_task_rows_are_prefixed_with_an_asterisk` | offline task rows never get the `*` prefix |
| `tests/unit/test_small_modules_coverage.py::test_task_warrior_bare_url_title_is_not_cut_at_scheme_colon` | a bare URL title becomes `https` |
| `tests/unit/test_small_modules_coverage.py::test_consolidate_sessions_accepts_report_slot_objects` | `consolidate_sessions` fails on slot objects with `event_duration=None` (production passes dicts) |
| `tests/unit/test_report_slot_entries_coverage.py::test_bucket_by_day_respects_day_start_hour_zero` | bucket labels ignore a day start below 4 |
| `tests/unit/test_report_slot_entries_coverage.py::test_split_at_month_boundary_preserves_total_duration` | month splits use a fixed 30-day step and raise |
| `tests/unit/test_report_slot_entries_coverage.py::test_split_at_year_boundary_preserves_total_duration` | year splits use a fixed 365-day step and raise |
| `tests/unit/test_categories_coverage.py::test_compile_non_numeric_score_raises_category_validation_error` | a non-numeric score raises `ValueError`, not `CategoryValidationError` |

Other known gaps:
- Duplicate TaskWarrior heartbeats are collapsed only on typed fetches (see Pipeline step 6).
- Project Tracking can exceed 100% in the `--by-project` view (for example 103.1%); the golden
  snapshot pins the current value.

When you fix one of these: the strict xfail will fail; remove its marker and update the snapshot
(if the output changes) in the same commit.

## 10. Testing layout

- `tests/unit/` - unit tests per module (`test_<module>.py`, plus `*_coverage.py` files written to
  reach 100%).
- `tests/characterization/` - end-to-end CLI runs through `main()` with a deterministic in-memory
  ActivityWatch (`tests/support/fake_aw.py`) and TaskWarrior stubs; `test_cli_golden.py` holds the
  golden matrix, `test_cli_paths.py` the flag and config paths.
- `tests/integration/` - end-to-end checks against the same fakes; they run in the normal suite.
- Stub the ActivityWatch client with `FakeActivityWatchClient`; stub `subprocess.run` for TaskWarrior
  calls; never call a real `task` binary or a live server from a test.

## 11. Git and review

**Docs in sync (rule):** when a change alters how the tool is installed, run, configured or tested
(Python version, venv or install steps, a CLI flag, a report format, a config key, a test command),
update the matching sections in the same commit: README "Quick Start", "Installation & Setup",
"Execution", "Configuration" and "Testing"; CLAUDE.md section 2 (Quick start) and section 7
(Configuration); and `index.txt` for new or moved files. Do not leave an install or run command in
the docs that the code no longer supports.

- Branch: `refactor/robust-core` (worktree `../work_report-robust`). `main` is the reference.
- Each commit leaves the gates green. Commit messages say what changed and why, and end with the
  `Co-Authored-By` trailer that the session instructions specify.
- Commit only when asked. Do not push.

## 12. Pending cleanup (confirm with the user before doing)

- `pipeline/presenters.py` is a thin wrapper; `main()` could call the renderers directly.
- `src/tw_report.egg-info/` is generated but tracked in git; it should be ignored.
- `core/report_slot.py` is ~1900 lines and mixes the slot model, merging and bucketing; split it
  along those lines when the model layer is refactored.
- Mypy and ruff run on `src` only; the test suite is not type-checked.

## 13. Open analysis: performance, fetch sources and bucket roles (next session)

Status: analysis only. Nothing in this section is implemented yet except where marked.
**Rule: when an item below is fully implemented, update this section in the same commit:
mark it DONE with the commit id, move the lasting facts into the sections above, and delete the
stale analysis. Do not leave a finished item described as open.**

### 13.1 Benchmark baseline (measured, not yet a script)

- Period `:lastweek` (2026-11-02 to 2026-11-09), local zone UTC-6, machine otherwise idle.
- Sample tool in `samples/` (bash + jq + `timesheet_report.py`, taskwarrior bucket only):
  about 0.18 s wall, five runs.
- `aw-report` restricted to the taskwarrior bucket (AFK and window return no events):
  about 0.70 s. Python startup is about 0.05 s and importing `tw_report.cli.main` takes about
  0.52 s (`aw_client` and `requests` account for about 0.36 s of that). The rest is the report.
- `aw-report --by-day :lastweek` with all three buckets: about 7.3 s (earlier runs of the same
  command were 6.9 s and 14 to 16 s, so the machine is noisy; compare runs taken close together).
- Correctness reference: the sample's per-day hours equal tw-report's taskwarrior events clipped to
  each local day, to two decimals, for every day. Totals: 30.20 h (sample) and 30.19 h (fetch).
  Grouping events by start time instead of clipping moves hours between days and is wrong.
- Sample defects, not to be copied: text output crashes on `project: null` (CSV works); the
  `-06:00` offset is hard-coded; the server is fixed at `localhost:5600`; it relies on GNU `date`.
- DONE (uncommitted): `dev/benchmark/compare.py` runs the shell workflow and its Python replica
  `dev/benchmark/aw_task_report.py` end to end (query, JSON, `samples/timesheet_report.py --format csv`),
  one warm-up plus 10 measured runs, reports avg/min/max, and checks that the CSV output is identical.
  The replica uses the stdlib only. End to end, 10 runs:
  `:lastweek` shell avg 0.260 s (min 0.221, max 0.304), python avg 0.367 s (min 0.336, max 0.411);
  `:lastmonth` shell avg 0.835 s (min 0.807, max 0.860), python avg 0.765 s (min 0.738, max 0.832).
  CSV identical in both. Shell is faster on small periods (interpreter start-up); Python is faster on
  large ones, where the jq filter's per-event timestamp parsing and grouping cost more as the event
  count grows (not profiled in detail). Modes that include today can differ between the two runs,
  because ActivityWatch keeps recording.
- Still open for the benchmark: import time of `tw_report` is not separated yet (see 13.1 above).

### 13.2 Full-pipeline profile and candidate changes (no behaviour change intended)

Profile of `--by-day :lastweek` (cProfile, all three buckets, about 8,000 window events):
- `categorize_event` runs the regex rules on every window event. About 3 s of the profile.
  Its result depends only on `(app, title)`, so each unique pair can be computed once.
- `EventIndex.active_at` reads `timestamp` and `duration` through aw_core property accessors on
  every lookup (about 1.9 million property calls). Precomputing the end times removes that.
- The builder only needs to know whether any window event is active at a tick
  (`has_window_activity`). An early-exit `any_active_at` avoids building lists.
- Remaining cost: about 1.9 s of socket reads for the window payload. Fetch and transfer volume
  is the limit; the sample avoids it by letting the server aggregate per period.

A prototype with the first three changes ran the full `--by-day :lastweek` in about 3.9 s, against
about 7.3 s for the current code, with byte-identical output. It was not committed. The change is
still open: implement it on its own commit with the usual gates and a parity check.

### 13.3 Detail levels versus data sources (decided direction, not implemented)

- Detail level only controls rendering (`cli/main.py` passes it to the renderers). It does not
  choose what to fetch. `should_skip_window_bucket` in `core/project_filtering.py` is dead code.
- Rejected: a "detail level 0" that drops AFK and window. Active, break, AFK and offline totals all
  depend on the AFK bucket, so that would be a different report, not a rendering level.
- Proposed: an explicit source option, for example `--source tasks` (or a list of roles), with the
  default `afk,window,tasks` unchanged. Each detail level declares which roles it needs. A request
  that cannot be satisfied (for example categories without `window`) fails with a clear message.
- Task-only totals must be documented as "tracked task time, no AFK", the same meaning as the sample.
- DONE (uncommitted): `--source` takes role names (`tasks`, `afk`, `window`; default all), parsed by
  `core/buckets.py::parse_sources`. Skipped roles are not fetched and the builder gets empty lists.
  Rejected with an error: `--project`/`--task` without `tasks`; `--app` or detail level 3+ without
  `window`. Tests: `tests/characterization/test_cli_sources.py` (queried buckets, validation) and
  `tests/unit/test_buckets.py::TestParseSources`. Checked on live data: `--source tasks` per-day
  totals equal the sample's per-day totals for the current `:lastweek` (for example 3.05 h and 3.27 h).

### 13.4 Bucket roles as splitters, aggregators and detail (proposal, under discussion)

**Principle (decided by the user):** the slots are the intersections of the buckets' events. Each
slot is an atomic interval in which the set of active events is constant, and it carries the data of
every bucket active in that interval:
- Where two or more buckets' events overlap, the slot is enriched with all of them (for example a
  task plus the AFK state plus the window).
- Where only one bucket has an event, the slot holds that bucket's data alone.
- Single bucket: each slot is one event, so the slots equal the events. A `tasks`-only run should
  give exactly one slot per task event. The current builder is close but not guaranteed to (touching
  events of the same task merge, overlapping events are tie-broken to one slot); verify with a test.
- Keys that two buckets both set are never silently overwritten. A slot keeps every value as a
  list, each tagged with the bucket it came from, so no precision is lost. Example: the window
  bucket `aw-watcher-window` says `app=Code` and the tmux bucket `aw-watcher-tmux` says `app=tmux`
  for the same minute; the enriched slot holds both values in a list. The AFK bucket sets
  `status=not-afk` on a different key, so it does not conflict.
- Every enriched value is tied to the timeline: it carries the interval it applies to (the slot
  span, or the event that set it), so the data always adds information about when it held.
- Decided: window events cut AND enrich, like every other bucket. There is no exception. The window
  bucket dominates the slot count, and that is accepted as the cost of exact intersections.
- Decided: AFK also cuts and enriches. The false-positive check is removed. Each slot carries one
  AFK state from three: `afk`, `not-afk`, or `untracked` (no AFK data covers that time). The
  window-coverage check (the `REVIEW(window-afk)` workaround) goes away with it.
- Decided: categories and productivity do not change their output. In the future they work over the
  whole slot, from the data the slot carries.
- Consequence to document: removing the false-positive check changes how some AFK and offline time
  is classed today, so the output of those cases will change. That change must be stated in the
  commit and checked against the new behaviour, not hidden behind a parity claim.

Code marked `REVIEW(window-afk)` (find it with `grep -rn "REVIEW(window-afk)" src`) is the window
workaround this section is about: the window fetch in `cli/main.py` and the offline check in
`core/timeslot_builder.py`. Remove the markers in the commit that replaces the workaround.

- Splitter: AFK. Its boundaries and state changes define slot limits. The builder already does this.
- Aggregator: taskwarrior. A task interval labels the time it covers with project and task. The
  builder already does this, with the overlap tie-break.
- Detail: window. Categories and apps are detail for the slots they overlap. Today every window
  start and end is also a cut, which is why window events dominate the tick count
  (about 8,000 window events against a few hundred AFK events in a week).
- Caveat: window is also a classifier input. The builder's `has_window_activity` check separates
  real AFK from false positives (system off but AFK still reporting). A detail-only window role must
  keep that check, or its results change.
- Proposed steps: (1) count cuts per role on a real week to confirm the hypothesis; (2) record
  each role's kind in `core/buckets.py` with no behaviour change; (3) stop window events from
  creating cuts and check the output against the parity baseline; (4) validate `--source` and
  detail levels against the role kinds.
- Risks: slot boundaries or totals could move, so parity must be checked on live data and in the
  characterization goldens. The role names and the four config keys are fixed today; changing them
  needs a decision.

### 13.5 Decisions and open questions

Decided by the user:
- Without AFK data, an offline-tagged task is classed as offline work (no window check is needed
  to classify it). This is the task-only behaviour and must match the sample tool's view of time.

- The source option is named `--source` and takes role names (`tasks`, `afk`, `window`), not bucket
  IDs, so the user never types a bucket name.

- DONE (uncommitted): `--format timesheet` (`pipeline/timesheet_render.py`) prints the sample's text
  layout from raw taskwarrior events, clipped to each day, week, month or year. It reads the tasks source
  only and rejects `--by-project`, `--no-taskwarrior`, `--app` and `--source` other than tasks.
  Parity on `:lastweek` (completed): byte-identical to the sample's text, once the sample's null-project
  crash is patched in a scratch copy. Known differences on `:lastmonth`, not replicated on purpose:
  (a) the server returns events whose start falls inside a period, so an event that starts on the day
  before and runs past midnight is missing from the next day in the sample (for example Oct 29, 2026:
  1h07 of work missing); the renderer clips by overlap instead, which keeps that time;
  (b) and (c) are inferred from the count differences, not verified yet: the sample may count events
  that clip to zero length inside a period (b), and duplicate heartbeat rows (c), which the renderer
  does not, since it uses the deduplicated events.

- Decided: the timesheet reports the worked time inside each period (overlap clipping), not the events
  that start inside it. An event that runs across midnight contributes to both days, clipped to each.
  The sample's start-based rule is a known difference, kept on purpose (see the timesheet item above).

Open:
2. Whether window stays a classifier input for AFK false positives (recommended) or moves elsewhere.
3. Whether the performance changes in 13.2 ship before or after the role-kind change in 13.4.
