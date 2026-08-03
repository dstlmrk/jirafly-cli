from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

WEEKDAY_NAMES = ("Mo", "Tu", "We", "Th", "Fr")


class TeamMember(BaseModel):
    """Configuration for a single team member."""

    name: str = Field(
        ...,
        min_length=1,
        description="Full name of the team member",
    )
    nickname: str = Field(
        ...,
        min_length=1,
        description="Nickname of the team member",
    )
    wd: float = Field(
        ...,
        gt=0,
        le=10,
        description="Work days (0-10)",
    )
    vel: float = Field(
        ...,
        gt=0,
        description="Velocity/productivity factor",
    )
    off_days: list[str] = Field(
        default=[],
        description="Weekdays not worked, e.g. ['Fr'] for a four-day week",
    )

    @field_validator("off_days")
    @classmethod
    def off_days_must_be_weekdays(cls, v):
        normalized = []
        for day in v:
            match = day.strip().title()[:2]
            if match not in WEEKDAY_NAMES:
                raise ValueError(
                    f"Invalid off_day {day!r}, expected one of {', '.join(WEEKDAY_NAMES)}"
                )
            normalized.append(match)
        return normalized

    def off_weekdays(self) -> set[int]:
        """off_days as Python weekday numbers (Mo = 0)."""
        return {WEEKDAY_NAMES.index(day) for day in self.off_days}

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError("Name cannot be empty")
        return v.strip()

    @field_validator("nickname")
    @classmethod
    def nickname_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError("Nickname cannot be empty")
        return v.strip()


class SprintWorkingDays(BaseModel):
    """Working days data for a sprint."""

    total: float = Field(
        ...,
        gt=0,
        description="Total working days for the sprint",
    )


class TeamConfig(BaseModel):
    """Configuration for team composition."""

    members: list[TeamMember] = Field(
        ...,
        min_length=1,
        description="List of team members",
    )
    working_days_per_sprint: dict[str, SprintWorkingDays] = Field(
        default={},
        description="Dictionary of sprints with working days data",
    )

    def to_member_dict(self) -> dict[str, tuple[float, float]]:
        """Convert to dictionary format expected by planning logic."""
        return {member.name: (member.wd, member.vel) for member in self.members}

    def to_nickname_dict(self) -> dict[str, tuple[str, float, float]]:
        """Convert to dictionary with nickname as key and (name, wd, vel) as value."""
        return {
            member.nickname: (member.name, member.wd, member.vel)
            for member in self.members
        }

    def get_member_by_nickname(self, nickname: str) -> TeamMember | None:
        """Get team member by nickname."""
        for member in self.members:
            if member.nickname == nickname:
                return member
        return None

    def apply_overrides(
        self, overrides: dict[str, tuple[float, float]]
    ) -> dict[str, tuple[float, float]]:
        """Apply nickname overrides and return updated member dict."""
        # Start with base team configuration
        team_members = self.to_member_dict()
        nickname_dict = self.to_nickname_dict()

        # Apply each override
        for nickname, (wd, vel) in overrides.items():
            # Skip if nickname doesn't exist in team config
            if nickname not in nickname_dict:
                continue

            # Apply override
            full_name, _, _ = nickname_dict[nickname]
            team_members[full_name] = (wd, vel)

        return team_members

    def get_override_summary(self, overrides: dict[str, tuple[float, float]]) -> str:
        """Generate formatted summary of applied overrides."""
        nickname_dict = self.to_nickname_dict()

        # Filter to only valid nicknames that exist in team config
        valid_overrides = {
            nickname: (wd, vel)
            for nickname, (wd, vel) in overrides.items()
            if nickname in nickname_dict
        }

        if not valid_overrides:
            return ""

        lines = ["🔧 Member overrides applied:"]

        for nickname, (wd, vel) in valid_overrides.items():
            full_name, _, _ = nickname_dict[nickname]
            lines.append(f"   {nickname} ({full_name}): wd={wd}, vel={vel}")

        return "\n".join(lines)


def load_team_config(config_file: Path) -> TeamConfig:
    """
    Load and validate team configuration from YAML file.
    """
    if not config_file.exists():
        raise FileNotFoundError(f"Team configuration file not found: {config_file}")

    try:
        with open(config_file, encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML syntax in {config_file}: {e}") from e

    return TeamConfig(**raw_config)
