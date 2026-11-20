# TW-Report: Timesheet Report Generator

A sophisticated timesheet reporting tool that correlates ActivityWatch activity data with Taskwarrior tasks, generating hierarchical and timeline-based work reports with productivity metrics.

**Answer: "Where did my time go, and what was I supposed to be working on?"**

---

## Quick Start (5 minutes)

### Prerequisites

You need:
- **ActivityWatch** running on `localhost:5600` ([download](https://activitywatch.net/))
- **Taskwarrior** with tasks created (`task add "My task"`)
- **Python 3.14+**

### Installation

Requires Python 3.14. The commands below create a project virtualenv in `.venv/`:

```bash
git clone <repository>
cd work_report
python3.14 -m venv .venv
.venv/bin/pip install -e ".[dev]"     # -e: edits to src/ take effect without reinstalling
.venv/bin/aw-report :today
```

To install the command for your user instead, use pipx with a Python 3.14 interpreter:

```bash
pipx install --python python3.14 /path/to/work_report
aw-report :today                      # the installed command is aw-report
```

### First Command

```bash
# Start ActivityWatch if not running
aw-server &

# Run the report for today
aw-report :today
```

You should see a report like:
```
▶ Project: MyProject (10.25)                                              1:30:45
  • Task: My task (10.25)                                                 1:30:45

                                                Total Time: 1:30:45  [prod 100%]
```

**Done!** See "Understanding the Output" below to read the report.

---

## What This Tool Does

`tw-report` bridges two tools:

1. **ActivityWatch** — Tracks what window you're focused on (app, window title, duration)
2. **Taskwarrior** — Records what task you're supposed to be working on

The tool correlates them to answer:
- **Where was my time?** (which projects, tasks, apps, categories)
- **How productive was I?** (scored by category: Coding=10, Social=negative, etc.)
- **What did I neglect?** (time in "No project assigned")

### Example Workflow

```
10:00 → 10:45  Focus on Coding (kitty window)  + Task: "Build feature X"  →  0:45 Coding
10:45 → 11:00  AFK (coffee break)                                          →  0:15 AFK
11:00 → 12:30  Focus on Slack                   + Task: "Build feature X"  →  1:30 Communication
                                                                ↓
                                    Productivity Report:
                                    Project: Build feature X
                                      Task: Build feature X
                                        Coding: 0:45 [score 10]
                                        Communication: 1:30 [score 3]
                                    Total: 2:15 [productivity 36%]
```

---

## Understanding the Output

### Report Formats

**Two views available** (default is hierarchical):

#### Timeline View (default)
Shows each time slot on a separate line:
```
       12:00-12:45  ▶ MyProject ▶▶ Feature A      0:45:00  [prod 100%]
      *12:45-13:00  ▶ (idle)                       0:15:00  [   AFK   ]
       13:00-14:30  ▶ MyProject ▶▶ Feature A      1:30:00  [prod  50%]

                                                Total Time: 2:45:00  [prod  60%]
```

**Column meanings:**
- `12:00-12:45` — Time range you worked
- `▶ MyProject ▶▶ Feature A` — Project > Task
- `0:45:00` — How long you worked on this
- `[prod 100%]` — Productivity percentage (time in high-score categories / total time)
- `*` — Marks AFK periods (idle)

#### Hierarchical View (with `--by-project`)
Groups by project tree:
```
▶ Project: MyProject (10.25)                                              2:15:00
  • Task: Feature A (10.25)                                               2:15:00

                                                Total Time: 2:15:00  [prod  60%]
```

**Meanings:**
- `(10.25)` — Productivity score for that level (higher = more productive)
- `2:15:00` — Total time spent
- `[prod 60%]` — Productivity percentage

### Understanding Metrics

| Metric | Meaning | Example |
|--------|---------|---------|
| **Total Time** | Time actually worked (tracked) | 2:15:00 |
| **[prod XX%]** | Productivity = (productive mins / total mins) × 100 | [prod 60%] = worked 60% on high-value tasks |
| **[AFK]** | Away from keyboard (idle, but system still recording) | `[AFK]` shows inactive periods |
| `*` asterisk | Marks idle periods in timeline | `*12:45-13:00` = idle for 15 min |

### Report Header (explains the day)

```
Online Time: 5:31:22      → Time system was actively tracking (AFK + non-AFK)
  • Active Time: 4:02:13  → Time you actively used keyboard/mouse (excludes AFK)
  • AFK time: 1:29:09     → Time you were idle (system still recording)
  • Project Tracking: 7.9% → How much time was assigned to tasks
  • Focus time: 1.6%      → Time spent on high-priority activities
```

---

## How to Use

### Basic Commands

```bash
# Today's report (default timeline view)
aw-report :today

# This week's report
aw-report :week

# Hierarchical (grouped by project)
aw-report :today --by-project

# Yesterday
aw-report :yesterday

# Specific date
aw-report 2026-06-18

# Date range
aw-report 2026-06-01 2026-06-30

# All time
aw-report :all
```

### Time Period Options

- `:today` — Current day
- `:yesterday` — Previous day
- `:week` — Current week (Mon-today)
- `:lastweek` — Previous week
- `:month` — Current month (1st-today)
- `:lastmonth` — Previous month
- `:year` — Current year (Jan 1-today)
- `:lastyear` — Previous calendar year
- `:all` — All recorded history
- `2026-06-18` — Specific date
- `2026-06-01 2026-06-30` — Date range

### Filtering & Display

```bash
# Show only specific project
aw-report :today --project Ecosistema

# Show only specific task
aw-report :today --task "Control de plagas"

# Show only specific app
aw-report :today --app kitty

# Deep dive: show every window title
aw-report :today --detail-level 5

# Group by week instead of day
aw-report :month --by-week

# Hide AFK/idle periods
aw-report :today --exclude-afk

# Show only productive activities (hide communication, social media, etc.)
aw-report :today --min-score 5

# Skip the productive / unproductive / undefined split in TOTALS
aw-report :today --no-productivity
```

TOTALS splits Active Time and AFK time by productivity. Each window is classified by its category's
score: a positive score is productive, a negative score is unproductive, and zero or no score is
undefined. Three indented lines appear under Active Time and under AFK time. Offline time is not split,
because offline work has no window data. The three parts sum to the figure above them:

```
TOTALS
────────────────────────────────────────────────────────────────────────────────
Total Time                                      11:24:54
  Worked Time                                     07:36:42
    Online                                          07:36:42
      Active Time                                     07:12:26
        Productive                                      00:30:36
        Unproductive                                    02:05:19
        Undefined                                       04:36:30
      AFK time                                        00:24:16
        Productive                                      00:00:00
        Unproductive                                    00:00:00
        Undefined                                       00:24:16
  Break Time                                      03:48:12
```

The split is computed from single-state slots (before `--consolidate` merges them), with each slot's
time divided by window category coverage. Each group is then scaled so the three parts sum exactly to
the Active Time or AFK time shown above it. It is an estimate per slot, not an exact per-window attribution.

### Common Patterns

```bash
# Weekly summary by project
aw-report :week --by-project

# Deep analysis of one task (all detail)
aw-report :today --task "Feature X" --detail-level 5

# See what you did instead of working on tasks
aw-report :today | grep "No project"

# Time breakdown by category (Coding, Communication, etc.)
aw-report :today --detail-level 3
```

---

## Features in Detail

#### Timesheet View (`--format timesheet`)
A per-period text timesheet with the same layout as `samples/timesheet_report.py`: check-in and
check-out, active hours, wall-clock span, then one row per task with project, hours and count. Active
hours and the task rows come from taskwarrior events. Check-in and check-out come from the timeslots
of the selected sources: by default only tasks (so the span is the tracked tasks), and with
`--source tasks,afk` (or `window`) the span is the machine's activity, whatever the task:
```
📅 Monday, Nov 09, 2026
   Check-in: 06:31 AM | Check-out: 07:04 AM | Active: 00:34 | Wall Clock: 00:34
------------------------------------------------------------------------------
    Project              | Task Title                             | Hours  | Count
```
Times are HH:MM (rounded to the minute): the period's Active and Wall Clock, and each task's Hours.
Use it with `--by-day` (default), `--by-week`, `--by-month` or `--by-year`. It cannot be combined
with `--by-project`, `--no-taskwarrior` or `--app`. Use `--source tasks,afk` to take check-in and
check-out from the system activity; the default `--source tasks` keeps the tracked-task span.
Tasks are grouped by title. Hours clipped to each period, so an event crossing a boundary counts
in both periods.

### Grouping Options (Report Types)

| Flag | Shows | Best For |
|------|-------|----------|
| (default) | Timeline — each slot on separate line | Detailed hourly breakdown |
| `--by-project` | Hierarchical — grouped by project tree | "Where did time go?" summary |
| `--by-day` | Consolidated — one line per project per day | "How much on each project per day?" |
| `--by-week` | Consolidated — one line per project per week | Weekly summary |
| `--by-month` | Consolidated — one line per project per month | Monthly summary |
| `--by-year` | Consolidated — one line per project per year | Yearly summary |
| `--format timesheet` | Text timesheet — check-in/out, hours and tasks per period | Check-in/out from the selected sources; hours from taskwarrior |

### Detail Levels

- **1**: Project only
- **2**: Project + Task (default)
- **3**: + Category (Coding, Communication, etc.)
- **4**: + App (kitty, Firefox, Slack, etc.)
- **5**: + Window Title (exact window title)

### Consolidation

`--consolidate` merges consecutive sessions of the same task, even with AFK gaps:

```bash
# Merges multiple work sessions on same task
# Useful when you switch apps but keep working on same task
aw-report :today --consolidate
```

### Special: OFFLINE Tasks

For work done **away from computer** (no window events recorded):

```bash
# Tag task in Taskwarrior
task <id> modify +offline

# Now it will show full duration (not just when window was focused)
aw-report :today
```

This is useful for:
- Meetings (no window focus = 0 duration normally)
- Writing/thinking time away from computer
- Code review on paper
- Any work that doesn't generate window events

---

## Installation & Setup

### From Source

```bash
git clone <repository>
cd work_report
python3.14 -m venv .venv
.venv/bin/pip install -e ".[dev]"    # development install, with the test and lint tools
```

### Execution

Run the command from the project venv, or from your shell after a pipx install:

```bash
.venv/bin/aw-report :today                         # timeline of the day (default)
.venv/bin/aw-report --by-project :lastweek         # hierarchical view by project
.venv/bin/aw-report --by-week :lastmonth           # one line per project per week
.venv/bin/aw-report --format timesheet :lastweek   # text timesheet: check-in/out, hours, tasks
.venv/bin/aw-report --format timesheet --by-month 2026-10-01 2026-10-31
.venv/bin/aw-report --source tasks :today          # read taskwarrior only (no AFK or windows)
.venv/bin/aw-report fetch :lastweek                # raw events of the three roles, as JSON
.venv/bin/python bin/aw-report :today              # development wrapper, runs src/ directly
```

`--format timesheet` takes its hours from taskwarrior and its check-in/out from the selected sources
(default `--source tasks`). It cannot be combined with `--by-project`, `--no-taskwarrior` or `--app`.
See "Features in Detail" for the other flags.

### Dependencies

- **Python 3.14+** — Stdlib `tomllib` (config), PEP 702 `warnings.deprecated`
- **ActivityWatch** (`aw-client ≥0.5.15`) — Event fetching
- **Taskwarrior** — Task management (via aw-watcher-taskwarrior)
- Config file support uses the standard-library `tomllib`

---

## Configuration

### Config File (Optional)

Create `~/.config/tw-report/config.toml` (the config directory keeps its original name, `tw-report`,
even though the command is `aw-report`):

```toml
# Default detail level (1-5)
detail_level = 3

# Projects to always exclude
exclude_projects = ["Personal", "Testing"]

# Custom terminal width
terminal_width = 120

[buckets]
# Optional. Each role is fed by one ActivityWatch bucket.
# Defaults: aw-watcher-<watcher>_<hostname>, where the hostname is this machine's name.
# hostname = "laptop"                     # change only the hostname used for the defaults
# window = "aw-watcher-tmux"              # or name the exact bucket ID of a role
# afk = "aw-watcher-afk_laptop"
# tasks = "aw-watcher-taskwarrior_laptop"
```

**Precedence:** CLI arguments > config file > defaults

**Buckets:** only the keys `hostname`, `afk`, `window` and `tasks` are accepted. Anything else
(a typo such as `windw`) is an error, reported on stderr with exit code 1.

```toml
# Day boundary, 0-23 (default 0 = local midnight)
day_start_hour = 0

[activitywatch]
# ActivityWatch server. Optional: when unset, falls back to
# ~/.config/activitywatch/aw-client/aw-client.toml, then 127.0.0.1:5600.
host = "127.0.0.1"
port = 5600
# IANA timezone for period boundaries (:today, :lastweek, ...). Default: system zone.
# timezone = "America/Mexico_City"
```

Only `host`, `port` and `timezone` are accepted in `[activitywatch]`.

### Fetching raw events (JSON)

```bash
aw-report fetch :lastweek        # or :yesterday, :lastmonth, 2026-03-01 2026-03-07, ...
```

Prints a JSON array with three entries, in the order afk, window, tasks. Each entry is the array of
that bucket's events for the period, with UTC timestamps. The buckets are the resolved `[buckets]` roles.

### Categories (Productivity Scores)

Categories come from ActivityWatch settings.json:

```json
{
  "classes": [
    {
      "name": ["Coding"],
      "rule": {"type": "regex", "regex": "vim|code|kitty"},
      "data": {"score": 10.0}
    },
    {
      "name": ["Social Media"],
      "rule": {"type": "regex", "regex": "twitter|facebook"},
      "data": {"score": -5.0}
    }
  ]
}
```

Override with: `aw-report --categories /path/to/custom.json`

---

## Troubleshooting

### "No activity found"
- **Check:** Is ActivityWatch running? (`ps aux | grep aw`)
- **Check:** Do you have window events for that period?
- **Try:** `aw-report :all` to see if any data exists

### Tasks show very little time
- **Normal behavior:** Only time with focused window is counted
- **Solution:** Tag task with `+offline` if work doesn't generate window events
- **Check:** Run with `--detail-level 5` to see if window events exist for that time

### Task doesn't appear at all
- **Check:** Is aw-watcher-taskwarrior running? (`ps aux | grep taskwarrior`)
- **Check:** Does task have window activity during that period?
- **Try:** `aw-report --by-project` to see if it groups differently

### Wrong productivity percentage
- **Check:** Category regex matches your window titles (`--detail-level 5`)
- **Check:** Category scores in settings.json are correct
- **Remember:** `[prod XX%]` = (productive time / total time) × 100

---

## Architecture (Advanced)

<details>
<summary><b>Click to expand: How it works internally</b></summary>

### Data Flow

```
ActivityWatch buckets
    ├─ aw-watcher-window      → window events (app, title)
    ├─ aw-watcher-afk         → keyboard/mouse idle periods
    └─ aw-watcher-taskwarrior → task events (project, task, duration)
                        ↓
          build_timeslot_timeline()  → merge into non-overlapping slots
                        ↓
        consolidate_by_period()      → group by date/week/month/year
                        ↓
    ┌───────────────────┴───────────────────┐
    ↓                                        ↓
aggregate_hierarchy()              print_timeline_report()
(project tree view)                (detailed timeline view)
```

### How It Correlates Data

Window events are matched to tasks via **temporal overlap**:
- If window event time overlaps with task event time, attribute the window to that task
- Multiple tasks = uses first task in list
- No task = shows as "No project assigned"

### Duration Calculation

**Default (window-based):**
- Task duration = sum of window event durations that overlap
- Only counts time when you actively had an app/window focused
- Gaps (thinking, breaks) not counted

**For OFFLINE tasks:**
- Uses full task duration (from task event start to end)
- Useful for work that doesn't generate window events

### Module Structure

- **cli/main.py** — Entry point, argument parsing
- **core/timeslot_builder.py** — Merges events into non-overlapping slots
- **core/report_slot.py** — Time slot data structures
- **pipeline/timeline_render.py** — Timeline report rendering
- **pipeline/report_render.py** — Hierarchical report rendering

</details>

---

## Performance

| Command | Duration | Notes |
|---------|----------|-------|
| `aw-report :today` | <1s | Single day |
| `aw-report :week` | 1-2s | Week aggregation |
| `aw-report :month` | 2-5s | Month aggregation |
| `aw-report :year` | 5-15s | Full year (depends on event density) |
| `aw-report :all` | Variable | All recorded history |

---

## Testing

### Run Tests

```bash
# All tests (from the project venv)
.venv/bin/python -m pytest

# Only unit tests (fast)
pytest tests/unit/ -v

# With coverage
pytest --cov=aw_report --cov-report=term-missing

# Specific test
pytest tests/unit/test_filtering.py::TestEventFilterBasics -v
```

### Test Status

- **690 unit tests** passing
- **1 pre-existing failure** (not a regression)
- **0 regressions** from cleanup

---

## Contributing

### Code Style

```bash
# Format code
ruff format .

# Check linting
ruff check .

# Type checking
mypy src/
```

### Adding Features

1. Create feature branch
2. Write tests first (TDD-style)
3. Implement feature
4. Run tests: `pytest`
5. Update docs if user-facing
6. Submit PR

---

## License

Part of the Ianua project.

## Author

Emir Herrera González <emir.herrera@gmail.com>
