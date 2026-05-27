from copy import deepcopy

from prettytable import PrettyTable
from termcolor import colored

from .models import UNASSIGNED, MemberPlan, Task


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
