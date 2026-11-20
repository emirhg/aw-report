# Optimization Opportunities

Known performance and efficiency ideas that are documented but not yet implemented.

## Lazy window-bucket fetching

**Status:** Not implemented. Windows are always fetched today. Documented for later.

### Current behaviour

- Window events are fetched for every report (`src/aw_report/cli/main.py`, the
  `task_time_ranges` branch around lines 414-429). With no task ranges, the full period is
  fetched; otherwise only the task ranges are.
- `can_skip_window` (`main.py:363`) is computed but never used.
- `should_skip_window_bucket()` (`src/aw_report/core/project_filtering.py:196`) encodes skip
  rules, but it is only called from tests.
- The "Window Bucket On-Demand Fetching" comment block (`main.py:386-405` and later) describes
  lazy loading that the code does not yet perform.

### Why windows are needed today

Window events are used for two things:

1. **Categories.** Needed for `--detail-level >= 3` (categories and apps) and for the productivity
   scores (the default TOTALS breakdown, `--min-score`).
2. **Online vs offline classification** where the AFK bucket has no coverage. In
   `core/timeslot_builder.py` (around lines 296-303 and 324-331), a window event in an AFK gap
   proves the computer was on, so the slice is online, not offline. This avoids counting the
   1-2 second AFK watcher startup lag as offline time (see commit ab6651d).

### Proposal

- The productivity breakdown is on by default and needs the full window bucket, so the full fetch is
  the default. Lazy fetching would apply only with `--no-productivity` and `--detail-level` below 3.
  Measured cost of the breakdown on `:week`: about 2% (58.7s vs 59.8s).
  Also consider the rules in `should_skip_window_bucket()` for `--app` and project grouping.
- Otherwise fetch windows only for time ranges that lack AFK coverage, reusing
  `_fetch_events_for_ranges()` (`main.py:133`).
- Do not skip window fetching entirely. That would bring back false offline time.

### Risks and tests to run before changing

- Offline-tagged task tests: `test_window_activity_during_afk_bucket_gap_classification` and
  `test_no_window_activity_during_gap_classified_as_offline`.
- Convergence tests for timeline and hierarchical output.
- Measure first with the timing instrumentation described in `CLAUDE.md` (Performance
  Optimization Patterns) before and after the change.
