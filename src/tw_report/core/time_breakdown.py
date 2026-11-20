"""How a slot's wall-clock time is split across the report's time states.

Every slot has exactly one AFK state (``afk``, ``not-afk``, ``untracked``) or none (no AFK data
covers it). The breakdown assigns the whole duration of the slot to one state bucket:

- ``not-afk``  -> active (worked, machine in use)
- ``afk``      -> afk (idle, machine in use)
- ``untracked`` -> untracked, or offline when the task carrying the slot has the offline tag
- no state     -> no_afk_data (time with data but outside any AFK range)
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

AfkState = Literal["afk", "not-afk", "untracked"]

ZERO = timedelta(0)


@dataclass(frozen=True)
class TimeBreakdown:
    active: timedelta = ZERO
    afk: timedelta = ZERO
    untracked: timedelta = ZERO
    offline: timedelta = ZERO
    no_afk_data: timedelta = ZERO

    @classmethod
    def for_state(
        cls, duration: timedelta, afk_state: AfkState | None, offline_tagged: bool = False
    ) -> TimeBreakdown:
        """The breakdown of one slot: its whole duration goes to the bucket of its state."""
        if afk_state == "not-afk":
            return cls(active=duration)
        if afk_state == "afk":
            return cls(afk=duration)
        if afk_state == "untracked":
            return cls(offline=duration) if offline_tagged else cls(untracked=duration)
        return cls(no_afk_data=duration)

    @property
    def online(self) -> timedelta:
        """Time the machine was in use: active plus AFK (the TOTALS 'Online' line)."""
        return self.active + self.afk

    @property
    def worked(self) -> timedelta:
        """Time spent on tasks while the machine was on, plus offline work."""
        return self.active + self.offline

    @property
    def total(self) -> timedelta:
        """Every second of the breakdown, across all states."""
        return self.online + self.offline + self.untracked + self.no_afk_data

    def __add__(self, other: TimeBreakdown) -> TimeBreakdown:
        return TimeBreakdown(
            active=self.active + other.active,
            afk=self.afk + other.afk,
            untracked=self.untracked + other.untracked,
            offline=self.offline + other.offline,
            no_afk_data=self.no_afk_data + other.no_afk_data,
        )


def sum_breakdowns(breakdowns: Iterable[TimeBreakdown]) -> TimeBreakdown:
    """Total of several breakdowns (for a period, a project, or a whole report)."""
    total = TimeBreakdown()
    for breakdown in breakdowns:
        total = total + breakdown
    return total
