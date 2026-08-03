from copy import deepcopy
from datetime import date
from typing import TYPE_CHECKING

from prettytable import PrettyTable
from termcolor import colored

from .models import UNASSIGNED, MemberPlan, Task

if TYPE_CHECKING:
    from .absence_store import AbsenceSnapshot, MemberAbsence
    from .pinya_service import AbsenceRecord
    from .sprints import Sprint


def safe_percentage(part, total):
    return (part / total * 100) if total != 0 else 0


def format_seconds(seconds):
    work_days, seconds = divmod(seconds, 28800)  # 8 * 3600
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts = []
    if work_days > 0:
        parts.append(f"{work_days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)


def highlight_exceeding(task: Task) -> str | None:
    if task.time_spent > (task.hle * 8 * 3600) * 3:
        return "red"
    elif task.time_spent > (task.hle * 8 * 3600) * 2:
        return "yellow"
    else:
        return None


def print_general_info(tasks_by_assignee: dict[str, MemberPlan], current_sprint: str):
    team_capacity = sum(
        (member.wd * member.vel) for member in tasks_by_assignee.values()
    )

    # Ratio
    hle_all: dict[str, dict[str, float]] = {
        "Maintenance": 0.0,
        "Bug": 0.0,
        "Product": 0.0,
        "AI": 0.0,
        "Excluded": 0.0,
    }
    hle_assigned = deepcopy(hle_all)

    for plan in tasks_by_assignee.values():
        for task in plan.tasks:
            hle_all[task.ratio_type] += task.hle
            if task.is_assigned:
                hle_assigned[task.ratio_type] += task.hle

    total = hle_all["Maintenance"] + hle_all["Bug"] + hle_all["Product"] + hle_all["AI"]
    total_assigned = (
        hle_assigned["Maintenance"]
        + hle_assigned["Bug"]
        + hle_assigned["Product"]
        + hle_assigned["AI"]
    )

    print(
        f"\nTotal capacity for {current_sprint} sprint: {colored(f' {team_capacity:.2f} MD ', 'black', on_color='on_yellow')}"
        f" (without ratio excluded: {team_capacity - hle_all['Excluded']:.2f} MD)",
        end="",
    )

    table = PrettyTable(header=False, align="l")

    table.add_row(["", "Assigned", "All"], divider=True)
    table.add_row(
        [
            colored(" AI", color="yellow"),
            colored(
                f"{hle_assigned['AI']:.2f} MD ({safe_percentage(hle_assigned['AI'], total_assigned):5.2f} %)",
                color="yellow",
            ),
            colored(
                f"{hle_all['AI']:.2f} MD ({safe_percentage(hle_all['AI'], total):5.2f} %)",
                color="yellow",
            ),
        ],
    )
    table.add_row(
        [
            colored(" Maintenance", color="cyan"),
            colored(
                f"{hle_assigned['Maintenance']:.2f} MD ({safe_percentage(hle_assigned['Maintenance'], total_assigned):5.2f} %)",
                color="cyan",
            ),
            colored(
                f"{hle_all['Maintenance']:.2f} MD ({safe_percentage(hle_all['Maintenance'], total):5.2f} %)",
                color="cyan",
            ),
        ],
    )
    table.add_row(
        [
            " Bugs",
            f"{hle_assigned['Bug']:.2f} MD ({safe_percentage(hle_assigned['Bug'], total_assigned):5.2f} %)",
            f"{hle_all['Bug']:.2f} MD ({safe_percentage(hle_all['Bug'], total):5.2f} %)",
        ],
    )
    table.add_row(
        [
            " Product",
            f"{hle_assigned['Product']:.2f} MD ({safe_percentage(hle_assigned['Product'], total_assigned):5.2f} %)",
            f"{hle_all['Product']:.2f} MD ({safe_percentage(hle_all['Product'], total):5.2f} %)",
        ],
    )
    table.add_row(
        [
            colored(" Excluded", color="magenta"),
            colored(f"{hle_assigned['Excluded']:.2f} MD", color="magenta"),
            colored(f"{hle_all['Excluded']:.2f} MD", color="magenta"),
        ],
        divider=True,
    )

    table.add_row(
        [
            " Total",
            f"{sum(hle_assigned.values()):.2f} / {team_capacity:.2f} MD",
            f"{sum(hle_all.values()):.2f} / {team_capacity:.2f} MD",
        ]
    )

    print()
    print(table)


def print_tasks_by_assignee(
    tasks_by_assignee: dict[str, MemberPlan], current_sprint: str, verbose: bool
):
    sorted_tasks_by_assignee = {
        k: tasks_by_assignee[k]
        for k in sorted(tasks_by_assignee.keys())
        if k != UNASSIGNED
    }
    sorted_tasks_by_assignee[UNASSIGNED] = tasks_by_assignee[UNASSIGNED]

    def get_task_detail(task_: Task):
        return (
            task_.hle_fmt(current_sprint),
            task_.title_ftm(verbose),
            task_.wsjf_fmt,
            task_.tl,
            task_.status_fmt,
            task_.fix_version_fmt(current_sprint),
        )

    table = PrettyTable()
    table.field_names = [
        "Assignee",
        "Tot.",
        "Cap.",
        "HLE",
        "Task",
        "WSJF",
        "TL",
        "Status",
        "FV",
    ]

    for user, data in sorted_tasks_by_assignee.items():
        capacity = data.wd * data.vel

        if data.tasks:
            table.add_row(
                [
                    user,
                    f"{data.total_hle:.2f}",
                    f"{capacity:.2f}",
                    *get_task_detail(data.tasks[0]),
                ],
                divider=len(data.tasks) == 1,
            )
            # Single tasks
            for idx, task in enumerate(data.tasks[1:]):
                is_last_task = idx == len(data.tasks) - 2
                table.add_row(
                    ["", "", "", *get_task_detail(task)], divider=is_last_task
                )

    # Set alignment - default left, with specific columns right-aligned
    for field in table.field_names:
        if field in ["Tot.", "Cap.", "WSJF", "HLE"]:
            table.align[field] = "r"
        else:
            table.align[field] = "l"
    print(table)


def fmt_days(value: float) -> str:
    """Days without trailing zeros: 1.5 stays 1.5, 3.0 becomes 3."""
    return f"{value:g}" if value else "-"


def print_absences(
    sprint: "Sprint",
    snapshot: "AbsenceSnapshot",
    member_order: list[str],
    rejected: list["AbsenceRecord"],
    as_yaml: bool,
):
    """Render a downloaded attendance snapshot."""
    if snapshot.holidays:
        print(f"🎉 Public holidays in sprint: {len(snapshot.holidays)}")
        for raw_day, name in snapshot.holidays:
            print(f"   {date.fromisoformat(raw_day):%d.%m.%Y} {name}")
    else:
        print("🎉 Public holidays in sprint: none")

    absence_types = sorted(
        {t for entry in snapshot.members.values() for t in entry.by_type}
    )

    table = PrettyTable()
    table.field_names = ["Member", "Nom.", *absence_types, "Absent", "Hol.", "WD"]

    # Config order is kept so the generated YAML comment matches how the file is
    # already written by hand.
    for name in member_order:
        entry = snapshot.members.get(name)
        if not entry:
            continue
        table.add_row(
            [
                name,
                fmt_days(entry.nominal_wd),
                *[fmt_days(entry.by_type.get(t, 0.0)) for t in absence_types],
                fmt_days(entry.absent_days),
                fmt_days(entry.holiday_days),
                colored(
                    fmt_days(entry.working_days),
                    "yellow" if entry.working_days < entry.nominal_wd else None,
                ),
            ]
        )

    for field in table.field_names:
        table.align[field] = "l" if field == "Member" else "r"
    print(table)

    print(
        f"Total working days: "
        f"{colored(f' {snapshot.total_working_days:g} MD ', 'black', on_color='on_yellow')}"
    )

    if rejected:
        print(f"\n⚠️  Rejected records (not counted): {len(rejected)}")
        for record in rejected:
            print(f"   {record.employee_name}: {record.type_name}, {record.term}")

    if as_yaml:
        breakdown = ", ".join(
            f"{snapshot.members[name].nickname} = {snapshot.members[name].working_days:g}"
            for name in member_order
            if name in snapshot.members
        )
        print("\n# paste into configs/team.yaml under working_days_per_sprint:")
        print(f'  "{sprint.label}": # {sprint.yaml_comment()}')
        print(f"    total: {snapshot.total_working_days:g}  # {breakdown}")


WEEKDAY_LABELS = ("Mo", "Tu", "We", "Th", "Fr")
CELL_WIDTH = 2

# Deliberately avoiding full blocks (█): they merge vertically between rows and
# make neighbouring people's absences look like one shape.
DAY_WORKING = "·"
DAY_ABSENT = "■"
DAY_HALF = "◧"
DAY_HOLIDAY = "H"
DAY_OFF = "░"


def print_absence_calendar(
    sprint: "Sprint",
    snapshot: "AbsenceSnapshot",
    member_order: list[str],
):
    """Draw the sprint's working days as a grid, one row per member.

    Only Mon-Fri are shown; a standard sprint therefore has 10 columns, split
    into calendar weeks.
    """
    days = sprint.business_days()
    names = [name for name in member_order if name in snapshot.members]
    if not days or not names:
        return

    holidays = {date.fromisoformat(raw) for raw, _ in snapshot.holidays}
    label_width = max(len(name) for name in names)

    def row(cells: list[str], label: str = "") -> str:
        return (f"{label:<{label_width}}  " + " ".join(cells)).rstrip()

    def header(render) -> list[str]:
        cells: list[str] = []
        previous_week = None
        for day in days:
            week = day.isocalendar().week
            if previous_week is not None and week != previous_week:
                cells.append("|")
            cells.append(render(day).center(CELL_WIDTH))
            previous_week = week
        return cells

    # Month number, printed only where it changes - a sprint can end in the next
    # month, and repeating it in every column is just noise.
    months_seen: set[int] = set()

    def month_label(day: date) -> str:
        if day.month in months_seen:
            return ""
        months_seen.add(day.month)
        return f"{day.month:02d}"

    print(f"\n📆 {sprint.label} day by day")
    print(row(header(month_label)))
    print(row(header(lambda d: WEEKDAY_LABELS[d.weekday()])))
    print(row(header(lambda d: f"{d.day:02d}")))

    for name in names:
        entry = snapshot.members[name]
        cells: list[str] = []
        previous_week = None
        for day in days:
            week = day.isocalendar().week
            if previous_week is not None and week != previous_week:
                cells.append("|")
            cells.append(_render_day(day, entry, holidays))
            previous_week = week
        print(row(cells, name))

    legend = "   ".join(
        [
            f"{DAY_WORKING} working",
            colored(f"{DAY_ABSENT} absent", "red"),
            colored(f"{DAY_HALF} half day", "yellow"),
            colored(f"{DAY_HOLIDAY} holiday", "cyan"),
            colored(f"{DAY_OFF} not working", "blue"),
        ]
    )
    print(f"\n{'':<{label_width}}  {legend}")


def _render_day(day: date, entry: "MemberAbsence", holidays: set[date]) -> str:
    # A day the member never works outranks a holiday - it costs them nothing.
    if not entry.works_on(day):
        return colored(DAY_OFF.center(CELL_WIDTH), "blue")
    if day in holidays:
        return colored(DAY_HOLIDAY.center(CELL_WIDTH), "cyan")
    booked = entry.days_off_on(day)
    if booked <= 0:
        return DAY_WORKING.center(CELL_WIDTH)
    if booked >= 1:
        return colored(DAY_ABSENT.center(CELL_WIDTH), "red")
    return colored(DAY_HALF.center(CELL_WIDTH), "yellow")
