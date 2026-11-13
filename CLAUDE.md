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
.venv/bin/tw-report :yesterday          # run against a local ActivityWatch server
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
day_start_hour = 4
exclude_projects = ["Personal"]
terminal_width = 120

[buckets]          # optional; defaults are aw-watcher-<watcher>_<hostname>
hostname = "laptop"
window = "aw-watcher-tmux"   # exact ID of one role
```

Only `hostname`, `afk`, `window` and `tasks` are accepted in `[buckets]`; anything else is an error.
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
