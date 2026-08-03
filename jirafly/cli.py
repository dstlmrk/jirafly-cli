from collections import defaultdict
from pathlib import Path
from typing import Any

import typer
from prettytable import PrettyTable
from termcolor import colored

from . import sprints
from .absence_store import build_snapshot
from .absence_store import load as load_snapshot
from .absence_store import save as save_snapshot
from .jira_service import JiraClient
from .models import MemberPlan
from .pinya_service import (
    PinyaClient,
    PinyaError,
    absence_days_by_member,
)
from .team_config import load_team_config
from .utils import (
    format_seconds,
    highlight_exceeding,
    print_absence_calendar,
    print_absences,
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


def apply_attendance(
    sprint: str,
    team_config,
    team_members: dict[str, tuple[float, float]],
    overrides: dict[str, tuple[float, float]],
) -> dict[str, tuple[float, float]]:
    """Fill working days from stored Pinya attendance.

    Precedence: explicit --member wins, then the stored snapshot, then the
    nominal wd from team.yaml. Velocity always stays from the config - Pinya
    knows nothing about it.
    """
    snapshot = load_snapshot(sprint)
    if not snapshot:
        print(
            f"ℹ️  No stored attendance for {sprint}, using nominal wd from config.\n"
            f"   Run: jirafly absences {sprint}"
        )
        return team_members

    explicit = {
        name
        for nickname, (name, _, _) in team_config.to_nickname_dict().items()
        if nickname in overrides
    }

    applied = []
    for name, (wd, vel) in team_members.items():
        if name in explicit:
            continue
        stored = snapshot.working_days_for(name)
        if stored is None:
            continue
        team_members[name] = (stored, vel)
        if stored != wd:
            applied.append(f"{name}: {wd:g} → {stored:g}")

    print(f"📥 Attendance from Pinya ({sprint}, fetched {snapshot.fetched_at_display})")
    if applied:
        for line in applied:
            print(f"   {line}")
    else:
        print("   no change against nominal wd")
    if skipped := explicit & set(team_members):
        print(f"   kept from --member: {', '.join(sorted(skipped))}")

    return team_members


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

        # Attendance downloaded by `absences` fills in working days for everyone
        # not set explicitly on the command line.
        team_members = apply_attendance(
            sprint, team_config, team_members, parsed_overrides
        )

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


@app.command()
def absences(
    sprint: str = typer.Argument(
        "current",
        help="Sprint identifier (e.g., 6.34), or current/prev/next/-2",
    ),
    team: Path = typer.Argument(
        Path("configs/team.yaml"), help="Path to team config YAML file", exists=True
    ),
    pinya_url: str = typer.Option(
        ...,
        envvar="PINYA_URL",
        help="Pinya HR base URL (e.g. https://yourcompany.pinya.hr).",
    ),
    as_yaml: bool = typer.Option(
        False,
        "--yaml",
        help="Print a working_days_per_sprint block ready to paste into team.yaml.",
    ),
    include_indirect: bool = typer.Option(
        False, "--include-indirect", help="Include indirect subordinates."
    ),
    no_save: bool = typer.Option(
        False, "--no-save", help="Do not store the snapshot for planning to use."
    ),
):
    """Download team attendance from Pinya HR and store it for planning.

    Working days per member are the nominal wd from team.yaml minus absences
    booked in Pinya minus public holidays falling inside the sprint. The result
    is saved, so `planning` can use it without touching Pinya again.
    """
    try:
        target = sprints.parse(sprint)
    except sprints.SprintError as exc:
        print(f"Error: {exc}")
        raise typer.Exit(1) from exc

    try:
        team_config = load_team_config(team)
    except Exception as exc:
        print(f"Error loading team config: {exc}")
        raise typer.Exit(1) from exc

    print(f"📋 Loaded team from {team}")
    print(f"📅 {target.describe()}")

    try:
        with PinyaClient(base_url=pinya_url) as pinya:
            roster = pinya.team(include_indirect=include_indirect)
            records = pinya.absence_records(include_indirect=include_indirect)
            holidays = pinya.public_holidays(target.start, target.end)
    except PinyaError as exc:
        print(f"Error: {exc}")
        raise typer.Exit(1) from exc

    # Only members listed in team.yaml are reported on; Pinya may know more
    # people than the team we plan for.
    known_names = {member.name for member in team_config.members}
    roster = {
        employee_id: name for employee_id, name in roster.items() if name in known_names
    }
    if missing := known_names - set(roster.values()):
        print(f"⚠️  Not found in Pinya: {', '.join(sorted(missing))}")

    breakdown = absence_days_by_member(records, roster, target.start, target.end)

    # Holidays on business days reduce everyone's capacity, and Pinya does not
    # report them as absences.
    business_holidays = [(day, name) for day, name in holidays if day.weekday() < 5]

    snapshot = build_snapshot(
        sprint=target,
        members=team_config.members,
        breakdown=breakdown,
        holidays=business_holidays,
    )

    member_order = [member.name for member in team_config.members]

    print_absences(
        sprint=target,
        snapshot=snapshot,
        member_order=member_order,
        rejected=breakdown.rejected,
        as_yaml=as_yaml,
    )
    print_absence_calendar(sprint=target, snapshot=snapshot, member_order=member_order)

    if not no_save:
        path = save_snapshot(snapshot)
        print(f"\n💾 Saved to {path} — planning will use these numbers")
