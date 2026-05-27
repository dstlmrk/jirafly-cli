from collections import defaultdict
from pathlib import Path
from typing import Any

import typer
from prettytable import PrettyTable
from termcolor import colored

from .jira_service import JiraClient
from .models import MemberPlan
from .team_config import load_team_config
from .utils import (
    format_seconds,
    highlight_exceeding,
    print_general_info,
    print_tasks_by_assignee,
    safe_percentage,
)

app = typer.Typer()

JIRA_PROJECT_KEY = "KNJ"


def parse_member_option(values: list[str]) -> dict[str, tuple[float, float]]:
    """Parse member option values from nickname=wd,vel format."""
    result = {}
    for value in values:
        try:
            nickname, rest = value.split("=", 1)
            wd_str, vel_str = rest.split(",", 1)
            wd = float(wd_str)
            vel = float(vel_str)
            result[nickname.strip()] = (wd, vel)
        except ValueError as e:
            raise typer.BadParameter(
                f"Invalid member format '{value}'. Expected: nickname=wd,vel (e.g., peter=7.0,0.3)"
            ) from e
    return result


@app.command()
def planning(
    sprint: str = typer.Argument(
        help="Sprint identifier (e.g., 6.12) to highlight tasks from previous sprints"
    ),
    team: Path = typer.Argument(
        Path("configs/team.yaml"), help="Path to team config YAML file", exists=True
    ),
    jira_url: str = typer.Option(
        ...,
        envvar="JIRA_URL",
        help="JIRA server URL (e.g., https://yourcompany.atlassian.net).",
    ),
    jira_email: str = typer.Option(
        ...,
        envvar="JIRA_EMAIL",
        help="JIRA email address for authentication.",
    ),
    jira_token: str = typer.Option(
        ...,
        envvar="JIRA_TOKEN",
        help="JIRA API token for authentication.",
    ),
    filter_id: str = typer.Option(
        "",
        envvar="PLANNING_FILTER_ID",
        help="JIRA filter ID for planning. Uses PLANNING_FILTER_ID env var if not provided.",
    ),
    member: list[str] = typer.Option(
        None,
        "--member",
        help="Override team member settings. Format: --member nickname=wd,vel (e.g., --member peter=7.0,0.3)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Plan sprint with team capacity from YAML configuration."""

    if not filter_id:
        print(
            "Error: filter_id must be provided either as parameter or PLANNING_FILTER_ID environment variable"
        )
        raise typer.Exit(1)

    # Load team configuration and apply overrides
    try:
        team_config = load_team_config(team)

        # Parse member overrides from command line
        parsed_overrides = parse_member_option(member or [])

        # Apply overrides to team configuration
        team_members = team_config.apply_overrides(parsed_overrides)

        print(f"📋 Loaded team from {team}")
        print(f"👥 Team members: {', '.join(team_members.keys())}")

        # Show override summary if any were applied
        override_summary = team_config.get_override_summary(parsed_overrides)
        if override_summary:
            print(override_summary)

    except Exception as e:
        print(f"Error loading team config: {e}")
        raise typer.Exit(1) from e

    # Planning logic
    client = JiraClient(jira_url, jira_email, jira_token)
    tasks = client.fetch_tasks(filter_id)

    tasks_by_assignee = defaultdict(
        lambda: MemberPlan(0, 0),
        {name: MemberPlan(*config) for name, config in team_members.items()},
    )

    for task in tasks:
        tasks_by_assignee[task.assignee].total_hle += task.hle
        tasks_by_assignee[task.assignee].tasks.append(task)

    print_general_info(tasks_by_assignee, sprint)
    print_tasks_by_assignee(tasks_by_assignee, sprint, verbose)


@app.command()
def ratio(
    team: Path = typer.Argument(
        Path("configs/team.yaml"), help="Path to team config YAML file", exists=True
    ),
    jira_url: str = typer.Option(
        ...,
        envvar="JIRA_URL",
        help="JIRA server URL (e.g., https://yourcompany.atlassian.net).",
    ),
    jira_email: str = typer.Option(
        ...,
        envvar="JIRA_EMAIL",
        help="JIRA email address for authentication.",
    ),
    jira_token: str = typer.Option(
        ...,
        envvar="JIRA_TOKEN",
        help="JIRA API token for authentication.",
    ),
    filter_id: str = typer.Option(
        "",
        envvar="RATIO_FILTER_ID",
        help="JIRA filter ID for ratio analysis. Uses RATIO_FILTER_ID env var if not provided.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    if not filter_id:
        print(
            "❌ Error: filter_id must be provided either as parameter or RATIO_FILTER_ID environment variable"
        )
        raise typer.Exit(1)

    tasks: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "ratio": {"Maintenance": 0, "Bug": 0, "Product": 0, "AI": 0, "Excluded": 0},
            "time": {"Maintenance": 0, "Bug": 0, "Product": 0, "AI": 0, "Excluded": 0},
            "tasks": [],
        }
    )

    team_config = load_team_config(team)
    client = JiraClient(jira_url, jira_email, jira_token)
    for task in client.fetch_tasks(filter_id):
        fix_version = task.fix_version or "No Fix Version"
        tasks[fix_version]["tasks"].append(task)
        tasks[fix_version]["ratio"][task.ratio_type] += task.hle
        tasks[fix_version]["time"][task.ratio_type] += task.time_spent

    sorted_tasks = dict(sorted(tasks.items()))

    table = PrettyTable(align="l")
    table.field_names = [
        "Fix version",
        "Assignee",
        "Task",
        "HLE",
        "Time spent",
        "Spr.",
        "Status",
    ]

    total_maintenance = total_product = total_excluded = total_ai = 0
    time_total_maintenance = time_total_product = 0

    for _, (fix_version, data) in enumerate(sorted_tasks.items()):
        maintenance = data["ratio"]["Maintenance"]
        product = data["ratio"]["Product"] + data["ratio"]["Bug"]
        excluded = data["ratio"]["Excluded"]
        ai = data["ratio"]["AI"]

        total_maintenance += maintenance
        total_product += product
        total_excluded += excluded
        total_ai += ai

        time_maintenance = data["time"]["Maintenance"]
        time_product = data["time"]["Product"] + data["time"]["Bug"]

        time_total_maintenance += time_maintenance
        time_total_product += time_product
        time_total_spent = 0

        sorted_tasks = sorted(data["tasks"], key=lambda x: x.assignee)
        for j, task in enumerate(sorted_tasks, start=1):
            previous_assignee = sorted_tasks[j - 2].assignee if j > 1 else None

            table.add_row(
                [
                    colored(
                        f" {fix_version} ",
                        color="black",
                        on_color="on_white",
                        attrs=["bold"],
                    )
                    if j == 1
                    else "",
                    task.assignee if task.assignee != previous_assignee else "",
                    task.title_ftm(verbose),
                    f"{task.hle:.2f}",
                    colored(format_seconds(task.time_spent), highlight_exceeding(task)),
                    task.sprint_fmt(fix_version),
                    task.status_fmt,
                ],
            )
            time_total_spent += task.time_spent

        table.add_divider()

        total = maintenance + product
        time_total = time_maintenance + time_product
        maintenance_str = (
            f"MAINTENANCE: {maintenance:5.2f}"
            f" / {safe_percentage(maintenance, total):4.1f} %"
            f" / ⏱ {safe_percentage(time_maintenance, time_total):4.1f} %"
        )
        product_str = (
            f"PRODUCT: {product:5.2f}"
            f" / {safe_percentage(product, total):4.1f} %"
            f" / ⏱ {safe_percentage(time_product, time_total):4.1f} %"
        )

        if fix_version in team_config.working_days_per_sprint:
            working_days_total = team_config.working_days_per_sprint[fix_version].total
            efficiency = f"{(total + excluded + ai) / working_days_total:.2f}"
            efficiency_str = (
                f"{colored(efficiency, 'black', on_color='on_yellow', attrs=['bold'])}"
            )
            working_days_total = f"{working_days_total} WD"
        else:
            working_days_total = ""
            efficiency_str = ""

        table.add_row(
            [
                "",
                "",
                colored(f"{maintenance_str}  |  {product_str}", attrs=["bold"]),
                colored(f"{total + excluded + ai:.2f}", attrs=["bold"]),
                format_seconds(time_total_spent),
                efficiency_str,
                working_days_total,
            ],
            divider=True,
        )

    _total = total_maintenance + total_product
    _time_total = time_total_maintenance + time_total_product
    maintenance_str = (
        f"MAINTENANCE: {total_maintenance:5.2f}"
        f" / {total_maintenance / _total * 100:4.1f} %"
        f" / ⏱ {time_total_maintenance / _time_total * 100:4.1f} %"
    )
    product_str = (
        f"PRODUCT: {total_product:5.2f}"
        f" / {total_product / _total * 100:4.1f} %"
        f" / ⏱ {time_total_product / _time_total * 100:4.1f} %"
    )

    table.add_row(
        [
            "",
            colored("Total", attrs=["bold"]),
            colored(f"{maintenance_str}  |  {product_str}", attrs=["bold"]),
            f"{_total + total_excluded + total_ai:.2f}",
            "",
            "",
            "",
        ]
    )

    table.align["HLE"] = "r"
    print(table)


@app.command()
def update_fix_versions(
    sprint: str = typer.Argument(
        help="Sprint identifier (e.g., 6.13) to set as fix version"
    ),
    jira_url: str = typer.Option(
        ...,
        envvar="JIRA_URL",
        help="JIRA server URL (e.g., https://yourcompany.atlassian.net).",
    ),
    jira_email: str = typer.Option(
        ...,
        envvar="JIRA_EMAIL",
        help="JIRA email address for authentication.",
    ),
    jira_token: str = typer.Option(
        ...,
        envvar="JIRA_TOKEN",
        help="JIRA API token for authentication.",
    ),
    filter_id: str = typer.Option(
        "",
        envvar="PLANNING_FILTER_ID",
        help="JIRA filter ID. Uses PLANNING_FILTER_ID env var if not provided.",
    ),
):
    """Update all tasks without fix version to the specified sprint version."""

    if not filter_id:
        print(
            "Error: filter_id must be provided either as parameter or PLANNING_FILTER_ID environment variable"
        )
        raise typer.Exit(1)

    client = JiraClient(jira_url, jira_email, jira_token)
    tasks = client.fetch_tasks(filter_id)

    tasks_without_fix_version = [task for task in tasks if not task.fix_version]

    if not tasks_without_fix_version:
        print("✅ All tasks already have fix version set")
        return

    print(f"\n🔍 Found {len(tasks_without_fix_version)} tasks without fix version")

    # Find fix version matching sprint ID
    version_id = client.find_version_starting_with(JIRA_PROJECT_KEY, sprint)

    if not version_id:
        print(
            f"❌ No fix version found starting with '{sprint}' in project {JIRA_PROJECT_KEY}"
        )
        raise typer.Exit(1)

    # Ask for confirmation
    print("\nTasks to update:")
    for task in tasks_without_fix_version[:10]:  # Show first 10
        print(f"  - {task._key}: {task._title[:60]}")
    if len(tasks_without_fix_version) > 10:
        print(f"  ... and {len(tasks_without_fix_version) - 10} more")

    confirm = typer.confirm(f"\nUpdate {len(tasks_without_fix_version)} tasks?")
    if not confirm:
        print("❌ Cancelled")
        raise typer.Exit(0)

    # Update tasks
    print("\n⏳ Updating tasks...")
    for i, task in enumerate(tasks_without_fix_version, 1):
        try:
            client.update_issue_fix_version(task._key, version_id)
            print(f"  [{i}/{len(tasks_without_fix_version)}] ✅ {task._key}")
        except Exception as e:
            print(f"  [{i}/{len(tasks_without_fix_version)}] ❌ {task._key}: {e}")

    print(f"\n✅ Updated {len(tasks_without_fix_version)} tasks")
