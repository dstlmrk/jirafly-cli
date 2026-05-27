from dataclasses import dataclass, field

from termcolor import colored

# Custom Fields
CUSTOM_FIELD_HLE = "customfield_11605"
CUSTOM_FIELD_WSJF = "customfield_11737"
CUSTOM_FIELD_TECH_LEAD_1ST = "customfield_11606"
CUSTOM_FIELD_TECH_LEAD_2ND = "customfield_11634"
CUSTOM_FIELD_SPRINT = "customfield_10000"

# Constants
UNASSIGNED = "Unassigned"


@dataclass
class MemberPlan:
    wd: float  # work days
    vel: float  # velocity
    tasks: list["Task"] = field(default_factory=list)
    total_hle: float = 0


class Task:
    @classmethod
    def from_raw_issue(cls, raw_issue_data: dict) -> "Task":
        """Create Task directly from raw Jira API response."""
        fields = raw_issue_data["fields"]

        task = cls.__new__(cls)  # Create instance without calling __init__

        # Basic fields
        task._key = raw_issue_data["key"]
        task._title = fields.get("summary", "")
        task.type = fields.get("issuetype", {}).get("name", "")
        task._status = type(
            "Status", (), {"name": fields.get("status", {}).get("name", "")}
        )()

        # Assignee
        assignee_data = fields.get("assignee")
        task.assignee = (
            assignee_data.get("displayName") if assignee_data else UNASSIGNED
        )
        task.is_assigned = bool(assignee_data)

        # Custom fields
        task.hle = float(fields.get(CUSTOM_FIELD_HLE, 0) or 0)
        task.wsjf = int(fields.get(CUSTOM_FIELD_WSJF, 0) or 0)

        # Tech leads
        def _get_initials(field_name: str) -> str:
            tl_data = fields.get(field_name)
            if not tl_data or not tl_data.get("displayName", "").strip():
                return ""
            return "".join(word[0].upper() for word in tl_data["displayName"].split())

        first = _get_initials(CUSTOM_FIELD_TECH_LEAD_1ST)
        second = _get_initials(CUSTOM_FIELD_TECH_LEAD_2ND)

        if first or second:
            blank = "  "
            task.tl = f"{first or blank}/{second or blank}"
        else:
            task.tl = ""

        # Labels and ratio type
        labels = fields.get("labels", [])
        if any(label in labels for label in ["RatioExcluded", "Bughunting"]):
            task.ratio_type = "Excluded"
        elif "AI" in labels:
            task.ratio_type = "AI"
        elif any(label in labels for label in ["Maintenance", "DevOps"]):
            task.ratio_type = "Maintenance"
        elif task.type == "Bug":
            task.ratio_type = "Bug"
        else:
            task.ratio_type = "Product"

        # Fix version
        fix_versions = fields.get("fixVersions", [])
        if fix_versions:
            sorted_versions = sorted(
                fix_versions, key=lambda x: x.get("name", ""), reverse=True
            )
            task.fix_version = cls._extract_version_number(sorted_versions[0]["name"])
        else:
            task.fix_version = None

        # Sprint
        sprints = fields.get(CUSTOM_FIELD_SPRINT, [])
        if sprints:
            sorted_sprints = sorted(
                sprints, key=lambda x: x.get("name", ""), reverse=True
            )
            task.sprint = cls._extract_version_number(sorted_sprints[0]["name"])
        else:
            task.sprint = None

        # Time spent
        task.time_spent = fields.get("timetracking", {}).get("timeSpentSeconds", 0)

        return task

    @staticmethod
    def _extract_version_number(version_name: str) -> str:
        """Extract version number (e.g., '6.12') from version name."""
        if not version_name:
            return None

        # Split by space and take first part to handle formats like "6.12.0 (16. 9. - 29. 9)"
        version_part = version_name.split()[0]

        # Extract X.Y pattern (first two parts separated by dots)
        parts = version_part.split(".")
        if len(parts) >= 2:
            return f"{parts[0]}.{parts[1]}"

        return version_part

    def title_ftm(self, verbose: bool) -> str:
        color, title = None, f"{self._key}: {self._title[:80]}"
        if self.ratio_type == "Maintenance":
            color = "cyan"
        elif self.ratio_type == "Excluded":
            color = "light_magenta"
        elif self.ratio_type == "AI":
            color = "yellow"

        formatted_title = f"{self.type_fmt} {colored(title, color)}"
        return f"{formatted_title}\n{self.url_fmt}" if verbose else f"{formatted_title}"

    @property
    def url_fmt(self):
        url = f"https://mallpay.atlassian.net/browse/{self._key}"
        return colored(url, "dark_grey")

    @property
    def type_fmt(self):
        color = on_color = None
        if self.type == "Bug":
            on_color = "on_red"
        elif self.type == "Analysis":
            color, on_color = "black", "on_light_grey"
        return colored(f"[{self.type[:1]}]", color, on_color, attrs=["bold"])

    @property
    def status_fmt(self):
        if self._status.name in ("In Progress", "In Review", "Waiting"):
            return colored(self._status.name, color="yellow")
        elif self._status.name in ("In Testing", "Merged", "Done"):
            return colored(self._status.name, color="green")
        else:
            return self._status.name

    def hle_fmt(self, current_sprint: str) -> str:
        if self.hle:
            if self.sprint != current_sprint:
                return colored(f"{self.hle:.2f}", "red", attrs=["bold"])
            else:
                return f"{self.hle:.2f}"
        else:
            return colored("✘", "red")

    def fix_version_fmt(self, current_sprint: str) -> str:
        if self.fix_version:
            if self.fix_version != current_sprint:
                return colored(self.fix_version, "red", attrs=["bold"])
            else:
                return self.fix_version
        else:
            return ""

    def sprint_fmt(self, current_sprint: str) -> str:
        if self.sprint:
            if self.sprint != current_sprint:
                return colored(self.sprint, "red", attrs=["bold"])
            else:
                return self.sprint
        else:
            return ""

    @property
    def wsjf_fmt(self) -> str:
        return self.wsjf or colored("✘", "red")
