from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, TypeVar

from aw_core.models import Event

from tw_report.core.categories import build_categories_from_window_events, get_category_score
from tw_report.core.event_slot import BUCKET_WINDOW
from tw_report.core.filtering import NO_TASK, EventFilter
from tw_report.pipeline.models import ReportContext, ReportMetrics

if TYPE_CHECKING:
    from tw_report.core.report_slot import ReportTimelineSlot


def aggregate_hierarchy_from_slots(
    consolidated_slots: list[ReportTimelineSlot],
    task_based: bool,
    cat_score_map: dict[str, float],
    get_category_score: Callable[[str, dict[str, float]], float],
    normalize_title: Callable[[str], str],
    event_filter: EventFilter | None = None,
) -> tuple[dict, bool]:
    """
    Build hierarchical report structure from consolidated slots (Phase 2 refactor).

    Replaces aggregate_hierarchy() by working with pre-built slots instead of canonical_events.
    Slots already have categories populated by build_timeslot_timeline(), so we only need
    to group by project/task and sum durations with productivity scoring.

    Same output shape as aggregate_hierarchy() — print_report() needs zero changes.

    Args:
        consolidated_slots: List of ReportTimelineSlot with categories pre-populated
        task_based: If True, group by project > task; if False, group by category only
        cat_score_map: Category -> productivity score mapping
        get_category_score: Function to look up category score
        normalize_title: Function to normalize window titles

    Returns:
        Dict with structure:
        - task_based=True: {project: {total_duration, prod_score, tasks: {...}}}
        - task_based=False: {category: {total_duration, prod_score, apps: {...}}}
    """
    # Apply filter BEFORE building hierarchy (ensures display matches metrics)
    # Use same type logic as main.py: 'afk' for "No project assigned", 'regular' otherwise
    from tw_report.core.filtering import NO_PROJECT

    if event_filter:
        consolidated_slots = [
            s
            for s in consolidated_slots
            if event_filter.should_include_entry(
                {
                    "project": s.project,
                    "task": s.task,
                    "type": "afk" if s.project == NO_PROJECT else "regular",
                }
            )
        ]

    report: dict = {}

    # Determine actual task_based mode: use True if ANY slot has project/task data (even NO_PROJECT)
    # This ensures "No project assigned" time is always shown hierarchically with project/task level
    # ReportTimelineSlot.project is never None (NO_PROJECT fallback), so for any non-empty
    # input this is always True and the former category-only grouping was unreachable; it
    # has been removed. Return value is unchanged: task_based for empty input.
    has_any_project_task = any(s.project is not None for s in consolidated_slots)
    should_group_by_task = task_based or has_any_project_task
    actual_task_based = should_group_by_task  # Capture what we actually use

    # Group by: Project > Task > Category > App > Title
    for slot in consolidated_slots:
        # Include all slots, even AFK-only (idle time is still tracked time)

        # Use slot's actual_duration as the work duration for this slot
        slot_work_duration = slot.actual_duration or slot.duration or timedelta(0)

        # If slot has no categories, create an "Uncategorized" default entry
        # This handles "No project assigned" slots which often lack categories
        categories_to_process = slot.categories or [
            {
                "category": "Uncategorized",
                "apps": [
                    {
                        "app": "No App",
                        "duration": slot_work_duration,
                        "titles": [
                            {
                                "title": "No Title",
                                "duration": slot_work_duration,
                            }
                        ],
                    }
                ],
            }
        ]

        # Each slot may have multiple categories; iterate over them
        for cat_entry in categories_to_process:
            category = cat_entry.get("category", "Uncategorized")
            score = get_category_score(category, cat_score_map)

            proj_node = report.setdefault(
                slot.project,
                {"total_duration": timedelta(0), "tasks": {}, "prod_score": 0.0},
            )
            task_node = proj_node["tasks"].setdefault(
                slot.task or NO_TASK,
                {"total_duration": timedelta(0), "categories": {}, "prod_score": 0.0},
            )
            cat_node = task_node["categories"].setdefault(
                category,
                {"total_duration": timedelta(0), "apps": {}, "prod_score": 0.0},
            )

            # Iterate over apps within this category entry
            apps_list = cat_entry.get("apps") or []
            if not apps_list:
                # If category has no apps, use the category duration directly
                # This handles task-based slots which have categories but no app info
                cat_duration = cat_entry.get("duration", slot_work_duration)
                prod_score = (cat_duration.total_seconds() / 3600) * score

                proj_node["total_duration"] += cat_duration
                proj_node["prod_score"] += prod_score
                task_node["total_duration"] += cat_duration
                task_node["prod_score"] += prod_score
                cat_node["total_duration"] += cat_duration
                cat_node["prod_score"] += prod_score
            else:
                # Normal case: iterate over apps
                for app_entry in apps_list:
                    app_name = app_entry.get("app", "Unknown App")
                    app_duration = app_entry.get("duration", timedelta(0))
                    prod_score = (app_duration.total_seconds() / 3600) * score

                    app_node = cat_node["apps"].setdefault(
                        app_name, {"total_duration": timedelta(0), "prod_score": 0.0}
                    )

                    proj_node["total_duration"] += app_duration
                    proj_node["prod_score"] += prod_score
                    task_node["total_duration"] += app_duration
                    task_node["prod_score"] += prod_score
                    cat_node["total_duration"] += app_duration
                    cat_node["prod_score"] += prod_score
                    app_node["total_duration"] += app_duration
                    app_node["prod_score"] += prod_score

                    # Iterate over titles within this app entry
                    for title_entry in app_entry.get("titles") or []:
                        title = title_entry.get("title", "No Title")
                        normalized_title = normalize_title(title)
                        title_duration = title_entry.get("duration", timedelta(0))
                        title_prod_score = (title_duration.total_seconds() / 3600) * score

                        title_node = app_node.setdefault("titles", {}).setdefault(
                            normalized_title,
                            {"total_duration": timedelta(0), "prod_score": 0.0},
                        )
                        title_node["total_duration"] += title_duration
                        title_node["prod_score"] += title_prod_score
    return report, actual_task_based


def compute_metrics(
    consolidated_slots: list[ReportTimelineSlot],
    cat_score_map: dict[str, float],
    get_category_score: Callable[[str, dict[str, float]], float],
    non_afk_time: timedelta,
    first_event_time,
    last_event_time,
    current_session_start,
    current_session_end,
    current_session_duration,
    last_break_start,
    last_break_end,
    last_break_duration,
    detail_level: int = 1,
) -> ReportMetrics:
    """Calculate productivity metrics from consolidated slots (Phase 2 refactor).

    Refactored to work with ReportTimelineSlot objects directly instead of canonical_events.
    Slots already have categories populated by the builder, enabling direct productivity scoring.
    """
    productive_time = timedelta(0)
    productive_task_time = timedelta(0)
    distracting_time = timedelta(0)
    unscored_time = timedelta(0)

    # Check if slots have category data (window events from ActivityWatch)
    # If no window events were fetched (AFK optimization mode), category scoring is meaningless
    # Also, only calculate productivity metrics if detail_level >= 3, since lower levels
    # don't display window categories (can't justify the percentages with visible data)
    has_category_data = any(slot.categories for slot in consolidated_slots)

    if has_category_data and detail_level >= 3:
        # Score productivity by iterating through slot categories
        for slot in consolidated_slots:
            if not slot.categories:
                continue

            # For each slot, score its categories and accumulate productivity
            for cat_entry in slot.categories:
                category = cat_entry.get("category", "Uncategorized")
                score = get_category_score(category, cat_score_map)
                slot_duration = slot.actual_duration or timedelta(0)

                if score > 0:
                    productive_time += slot_duration
                    if slot.task_event is not None:
                        productive_task_time += slot_duration
                elif score < 0:
                    distracting_time += slot_duration
                else:
                    unscored_time += slot_duration
    # else: AFK optimization mode (no window events), skip category scoring entirely

    return ReportMetrics(
        productive_time=productive_time,
        productive_task_time=productive_task_time,
        distracting_time=distracting_time,
        unscored_time=unscored_time,
        non_afk_time=non_afk_time,
        first_event_time=first_event_time,
        last_event_time=last_event_time,
        current_session_start=current_session_start,
        current_session_end=current_session_end,
        current_session_duration=current_session_duration,
        last_break_start=last_break_start,
        last_break_end=last_break_end,
        last_break_duration=last_break_duration,
    )


def build_context(
    consolidated_slots: list[ReportTimelineSlot],
    task_events: Sequence[Event] | None,
    afk_events: Sequence[Event],
    cat_score_map: dict[str, float],
    is_task_based_report: bool,
    metrics: ReportMetrics,
) -> ReportContext:
    """Build report context from consolidated slots (Phase 2 refactor).

    Refactored to use slots directly instead of canonical_events bridge.
    """
    from tw_report.pipeline.models import BucketEvents

    # Organize events into typed BucketEvents collection
    bucket_events = BucketEvents(
        afk=list(afk_events),
        taskwarrior=list(task_events or []),
        window=[],  # Window events not stored in context (fetched on-demand)
    )

    return ReportContext(
        bucket_events=bucket_events,
        consolidated_slots=consolidated_slots,
        cat_score_map=cat_score_map,
        is_task_based_report=is_task_based_report,
        metrics=metrics,
    )


E = TypeVar("E", bound=Event)


def merge_overlapping_afk_periods[E: Event](afk_events: list[E]) -> list[E]:
    """Merge overlapping/touching AFK periods and bridge micro-gaps.

    Merges:
    1. All overlapping AFK events (both 'afk' and 'not-afk' status)
    2. Events separated by micro-gaps (< 2 seconds), treating them as continuous online time
    3. This eliminates spurious offline-time noise (2-7s gaps) caused by ActivityWatch
       sometimes recording events separately vs. merging them

    The 2-second threshold is chosen because:
    - Micro-gaps smaller than this are almost certainly AW recording artifacts
    - A genuine system powerdown would produce seconds-to-minutes gaps, not milliseconds
    - This matches typical AW event boundary jitter (~0.5s precision)

    Args:
        afk_events: list of AFK bucket events with 'status' field ('afk' or 'not-afk')

    Returns:
        new list with overlapping periods and micro-gaps merged
    """
    from datetime import timedelta

    if not afk_events or len(afk_events) <= 1:
        return afk_events

    # Sort all AFK events (both 'afk' and 'not-afk') by start time
    sorted_events = sorted(afk_events, key=lambda e: e.timestamp)

    # Merge overlapping and micro-gap-separated events (SAME STATUS ONLY)
    merged = []
    current_event = deepcopy(sorted_events[0])
    current_start = current_event.timestamp
    current_end = current_event.timestamp + current_event.duration
    current_status = current_event.data.get("status")

    for event in sorted_events[1:]:
        event_status = event.data.get("status")
        event_start = event.timestamp
        event_end = event.timestamp + event.duration
        gap = event_start - current_end

        # Only merge if same status AND (overlapping OR micro-gap < 2 seconds)
        # Don't merge across status changes (afk ≠ not-afk)
        if event_status == current_status and gap < timedelta(seconds=2):
            # Same status + overlap/small gap: extend the range
            current_end = max(current_end, event_end)
        else:
            # Status changed OR real gap: finalize current period and start new one
            merged_duration = current_end - current_start
            current_event.timestamp = current_start
            current_event.duration = merged_duration
            merged.append(current_event)

            current_event = deepcopy(event)
            current_start = event_start
            current_end = event_end
            current_status = event_status

    # Finalize last period
    merged_duration = current_end - current_start
    current_event.timestamp = current_start
    current_event.duration = merged_duration
    merged.append(current_event)

    return sorted(merged, key=lambda e: e.timestamp)


@dataclass
class ProductivityBreakdown:
    """Active and AFK time split by category productivity score.

    Score > 0 is productive, score < 0 is unproductive, and score 0 (or no
    category data) is undefined. Each pair of parts sums to the matching
    TOTALS figure (Active Time, AFK time).
    """

    active_productive: timedelta = timedelta(0)
    active_unproductive: timedelta = timedelta(0)
    active_undefined: timedelta = timedelta(0)
    afk_productive: timedelta = timedelta(0)
    afk_unproductive: timedelta = timedelta(0)
    afk_undefined: timedelta = timedelta(0)


def _score_bucket(score: float | None) -> str:
    """Map a category score to 'productive', 'unproductive' or 'undefined'."""
    if not score:
        return "undefined"
    return "productive" if score > 0 else "unproductive"


def _split_by_window_categories(
    start,
    end,
    duration: timedelta,
    window_events: list,
    cat_score_map: dict[str, float],
) -> dict[str, timedelta]:
    """Split duration across buckets using window-category coverage of [start, end].

    Window time with a scored category goes to that bucket; window-uncovered
    time is undefined. Shares are proportional to coverage, so the returned
    parts always sum to duration.
    """
    span = (end - start).total_seconds()
    shares = {"productive": 0.0, "unproductive": 0.0, "undefined": 0.0}
    if span <= 0:
        shares["undefined"] = 1.0
    else:
        covered = 0.0
        for cat in build_categories_from_window_events(window_events, start, end):
            seconds = cat["duration"].total_seconds()
            shares[_score_bucket(get_category_score(cat["category"], cat_score_map))] += seconds
            covered += seconds
        shares["undefined"] += max(span - covered, 0.0)

    total = sum(shares.values())
    parts = {
        bucket: duration * (value / total) if total else timedelta(0)
        for bucket, value in shares.items()
    }
    # Undefined takes the remainder so the parts sum exactly to duration
    parts["undefined"] = duration - parts["productive"] - parts["unproductive"]
    return parts


def _fit_to_total(parts: dict[str, timedelta], target: timedelta) -> dict[str, timedelta]:
    """Rescale productive/unproductive so the three parts sum exactly to target.

    Undefined takes the remainder. If the raw parts sum to zero, the whole
    target is undefined.
    """
    raw_total = sum(parts.values(), timedelta(0))
    if raw_total <= timedelta(0):
        return {"productive": timedelta(0), "unproductive": timedelta(0), "undefined": target}
    scale = target / raw_total
    fitted = {
        "productive": parts["productive"] * scale,
        "unproductive": parts["unproductive"] * scale,
    }
    fitted["undefined"] = target - fitted["productive"] - fitted["unproductive"]
    return fitted


def _window_events_for(slot: ReportTimelineSlot, fallback: list) -> list:
    """Window events that can cover this slot's span.

    Builder slots carry every window event overlapping their span, so their own events
    are used. Slots without any event data fall back to the global window list.
    """
    if slot.events:
        return slot.events.get(BUCKET_WINDOW, [])
    return fallback


def compute_productivity_breakdown(
    filtered_slots: list[ReportTimelineSlot],
    window_events: list,
    cat_score_map: dict[str, float],
    active_total: timedelta | None = None,
    afk_total: timedelta | None = None,
) -> ProductivityBreakdown:
    """Split Active and AFK time into productive / unproductive / undefined.

    Pass single-state (non-consolidated) slots: window coverage is measured over
    each slot's span, so a consolidated slot that also covers offline time would
    dilute the split. Offline-tagged slots are not skipped: they contribute only
    the online/AFK time they actually carry.

    AFK amounts mirror compute_afk_offline_totals(): afk_duration when slots
    are consolidated, otherwise the duration of is_afk_only slots.

    If active_total / afk_total are given, each group is rescaled so its parts
    sum exactly to the displayed TOTALS figure (this absorbs micro-slots dropped
    by consolidation).
    """
    active_parts = {
        "productive": timedelta(0),
        "unproductive": timedelta(0),
        "undefined": timedelta(0),
    }
    afk_parts = {
        "productive": timedelta(0),
        "unproductive": timedelta(0),
        "undefined": timedelta(0),
    }
    has_consolidated_afk = any(s.afk_duration is not None for s in filtered_slots)

    def add(parts: dict[str, timedelta], split: dict[str, timedelta]) -> None:
        for bucket, amount in split.items():
            parts[bucket] += amount

    for slot in filtered_slots:
        active = slot.actual_duration or timedelta(0)
        if active > timedelta(0):
            add(
                active_parts,
                _split_by_window_categories(
                    slot.start,
                    slot.end,
                    active,
                    _window_events_for(slot, window_events),
                    cat_score_map,
                ),
            )

        if has_consolidated_afk:
            afk = slot.afk_duration or timedelta(0)
        else:
            afk = (slot.actual_duration or slot.duration) if slot.is_afk_only else timedelta(0)
        if afk > timedelta(0):
            add(
                afk_parts,
                _split_by_window_categories(
                    slot.start,
                    slot.end,
                    afk,
                    _window_events_for(slot, window_events),
                    cat_score_map,
                ),
            )

    if active_total is not None:
        active_parts = _fit_to_total(active_parts, active_total)
    if afk_total is not None:
        afk_parts = _fit_to_total(afk_parts, afk_total)

    return ProductivityBreakdown(
        active_productive=active_parts["productive"],
        active_unproductive=active_parts["unproductive"],
        active_undefined=active_parts["undefined"],
        afk_productive=afk_parts["productive"],
        afk_unproductive=afk_parts["unproductive"],
        afk_undefined=afk_parts["undefined"],
    )
