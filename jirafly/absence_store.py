"""Storage for downloaded Pinya attendance.

The `absences` command writes a snapshot per sprint, `planning` reads it back so
it never has to talk to Pinya (no browser, no session). Kept outside the repo in
~/.jirafly because it holds colleagues' absence data and must not be committed.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from .sprints import Sprint

STORE_PATH = Path.home() / ".jirafly" / "absences.json"


@dataclass
class MemberAbsence:
    """Absence totals for one member in one sprint, in working days."""

    nickname: str
    nominal_wd: float
    absent_days: float
    holiday_days: float
    working_days: float
    by_type: dict[str, float] = field(default_factory=dict)
    # ISO date -> absence type -> days; drives the sprint calendar
    by_day: dict[str, dict[str, float]] = field(default_factory=dict)
    # Python weekday numbers not worked at all (Mo = 0)
    off_weekdays: list[int] = field(default_factory=list)

    def days_off_on(self, day: date) -> float:
        return sum(self.by_day.get(day.isoformat(), {}).values())

    def works_on(self, day: date) -> bool:
        return day.weekday() not in self.off_weekdays


@dataclass
class AbsenceSnapshot:
    sprint_label: str
    start: str
    end: str
    fetched_at: str
    total_working_days: float
    holidays: list[tuple[str, str]]
    members: dict[str, MemberAbsence]

    @property
    def fetched_at_display(self) -> str:
        try:
            return datetime.fromisoformat(self.fetched_at).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            return self.fetched_at

    def working_days_for(self, member_name: str) -> float | None:
        entry = self.members.get(member_name)
        return entry.working_days if entry else None


def build_snapshot(
    sprint: Sprint,
    members: list,
    breakdown,
    holidays: list[tuple[date, str]],
    fetched_at: datetime | None = None,
) -> AbsenceSnapshot:
    """Turn raw absence days into per-member working days.

    Working days are the nominal wd from team.yaml minus absences minus public
    holidays: the nominal value reflects the contract, the other two come off
    the top of it.

    A holiday only costs a member a day if they work that weekday - someone on a
    four-day week loses nothing when the holiday falls on their day off.
    """
    entries: dict[str, MemberAbsence] = {}
    total = 0.0

    for member in members:
        off_weekdays = member.off_weekdays()
        holiday_days = float(
            sum(1 for day, _ in holidays if day.weekday() not in off_weekdays)
        )
        by_type = {
            name: round(days, 2)
            for name, days in sorted(breakdown.by_type.get(member.name, {}).items())
        }
        by_day = {
            day: {name: round(days, 2) for name, days in sorted(per_type.items())}
            for day, per_type in sorted(breakdown.by_day.get(member.name, {}).items())
        }
        absent = sum(by_type.values())
        working_days = max(0.0, member.wd - absent - holiday_days)
        total += working_days
        entries[member.name] = MemberAbsence(
            nickname=member.nickname,
            nominal_wd=member.wd,
            absent_days=absent,
            holiday_days=holiday_days,
            working_days=working_days,
            by_type=by_type,
            by_day=by_day,
            off_weekdays=sorted(off_weekdays),
        )

    return AbsenceSnapshot(
        sprint_label=sprint.label,
        start=sprint.start.isoformat(),
        end=sprint.end.isoformat(),
        fetched_at=(fetched_at or datetime.now()).isoformat(timespec="seconds"),
        total_working_days=total,
        holidays=[(day.isoformat(), name) for day, name in holidays],
        members=entries,
    )


def save(snapshot: AbsenceSnapshot, path: Path = STORE_PATH) -> Path:
    """Store the snapshot, replacing any previous data for that sprint."""
    path.parent.mkdir(parents=True, exist_ok=True)
    store = _read_raw(path)
    store[snapshot.sprint_label] = asdict(snapshot)
    path.write_text(
        json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def load(sprint_label: str, path: Path = STORE_PATH) -> AbsenceSnapshot | None:
    raw = _read_raw(path).get(sprint_label)
    if not raw:
        return None
    try:
        members = {
            name: MemberAbsence(**entry) for name, entry in raw["members"].items()
        }
        return AbsenceSnapshot(
            sprint_label=raw["sprint_label"],
            start=raw["start"],
            end=raw["end"],
            fetched_at=raw["fetched_at"],
            total_working_days=raw["total_working_days"],
            holidays=[tuple(item) for item in raw.get("holidays", [])],
            members=members,
        )
    except (KeyError, TypeError):
        # Written by an older version - treat as absent rather than crashing.
        return None


def stored_sprints(path: Path = STORE_PATH) -> list[str]:
    return sorted(_read_raw(path))


def _read_raw(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
