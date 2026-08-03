"""Sprint calendar.

Sprints run 14 days, from Tuesday to the second Monday. Everything is derived
from one known sprint, so there is no need to keep a table of dates around.

Jira fix versions do carry the dates too (e.g. "6.12.0 (16. 9. - 29. 9)"), but
they lack a year and depend on someone naming them correctly, so the anchor is
the more reliable source.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

ANCHOR_START = date(2026, 7, 21)
ANCHOR_MAJOR = 6
ANCHOR_MINOR = 33
LENGTH_DAYS = 14


class SprintError(ValueError):
    """Raised when a sprint specification cannot be resolved."""


@dataclass(frozen=True)
class Sprint:
    label: str
    start: date
    end: date

    def describe(self) -> str:
        return f"Sprint {self.label} ({self.start:%d.%m.%Y} - {self.end:%d.%m.%Y})"

    def yaml_comment(self) -> str:
        """Date range in the style used in team.yaml comments."""
        return f"{self.start.day}.{self.start.month}. - {self.end.day}.{self.end.month}.{self.end.year}"

    def business_days(self) -> list[date]:
        """Mon-Fri days inside the sprint (10 for a standard sprint)."""
        days = []
        for offset in range(LENGTH_DAYS):
            day = self.start + timedelta(days=offset)
            if day.weekday() < 5:
                days.append(day)
        return days


def at_offset(offset: int) -> Sprint:
    """Sprint `offset` sprints from the anchor (negative means earlier)."""
    minor = ANCHOR_MINOR + offset
    if minor < 1:
        raise SprintError(f"Sprint {ANCHOR_MAJOR}.{minor} does not exist")
    start = ANCHOR_START + timedelta(days=LENGTH_DAYS * offset)
    return Sprint(
        label=f"{ANCHOR_MAJOR}.{minor}",
        start=start,
        end=start + timedelta(days=LENGTH_DAYS - 1),
    )


def current_offset(today: date | None = None) -> int:
    return ((today or date.today()) - ANCHOR_START).days // LENGTH_DAYS


def current(today: date | None = None) -> Sprint:
    return at_offset(current_offset(today))


def parse(spec: str, today: date | None = None) -> Sprint:
    """Resolve a sprint spec: '6.34', 'current', 'prev', 'next', '-1', '+2'."""
    text = (spec or "current").strip().lower()
    offset = current_offset(today)

    if text in {"current", "c", "this"}:
        return at_offset(offset)
    if text in {"prev", "previous", "p", "last"}:
        return at_offset(offset - 1)
    if text in {"next", "n"}:
        return at_offset(offset + 1)
    if re.fullmatch(r"[+-]\d+", text):
        return at_offset(offset + int(text))

    if match := re.fullmatch(r"(\d+)\.(\d+)", text):
        major, minor = int(match.group(1)), int(match.group(2))
        if major != ANCHOR_MAJOR:
            raise SprintError(
                f"Only sprints of major version {ANCHOR_MAJOR} can be computed "
                f"(anchor {ANCHOR_MAJOR}.{ANCHOR_MINOR} = {ANCHOR_START:%d.%m.%Y}). "
                f"Update the anchor in sprints.py for a different major."
            )
        return at_offset(minor - ANCHOR_MINOR)

    raise SprintError(
        f"Cannot parse sprint {spec!r}. Use e.g. 6.34, current, prev, next or -2."
    )
