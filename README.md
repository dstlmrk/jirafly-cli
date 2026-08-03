# Jirafly 🪰

Jirafly helps you calculate key statistics that Jira itself cannot provide, offering essential insights for effective team management.

## How to develop

This package uses [uv](https://github.com/astral/uv) for dependency management. To install Jirafly and its dependencies:

1. Install uv (if not installed):
```bash
$ curl -LsSf https://astral.sh/uv/install.sh | sh
```

2. Create a virtual environment and sync dependencies:
```bash
$ uv venv
$ uv sync --dev
```

3. Install pre-commit hooks:
```bash
$ uv run pre-commit install
```

## Running

You can run the CLI app from the activated virtual environment:

```bash
$ uv run jirafly planning --help
$ uv run jirafly ratio --help
$ uv run jirafly update-fix-versions --help
```

### Available Commands

**Planning Command:**
```bash
$ uv run jirafly planning 6.13 configs/team.yaml
```
Analyzes sprint capacity planning for the specified sprint (e.g., 6.13), showing task assignments and team member capacity utilization.

**Ratio Analysis:**
```bash
$ uv run jirafly ratio configs/team.yaml
```
Calculates maintenance vs product development work ratios across sprints with time tracking metrics.

**Update Fix Versions:**
```bash
$ uv run jirafly update-fix-versions 6.13
```
Bulk updates all tasks without a fix version to the specified sprint version (e.g., 6.13). Finds matching fix version in Jira and prompts for confirmation before updating.

**Absences (Pinya HR):**
```bash
$ uv run jirafly absences              # current sprint
$ uv run jirafly absences 6.34         # specific sprint
$ uv run jirafly absences prev --yaml  # previous sprint, plus YAML to paste
```
Downloads team attendance from Pinya HR and **stores it** in
`~/.jirafly/absences.json`. Working days per member are
`nominal wd from team.yaml − absences − public holidays in the sprint`.

`planning` then picks those numbers up automatically, so Pinya is contacted only
by this command:

```bash
$ uv run jirafly absences 6.34      # download once
$ uv run jirafly planning 6.34      # uses the stored numbers, no browser
```

Precedence for working days:
1. `--member nickname=wd,vel` on the command line
2. stored attendance for that sprint
3. nominal `wd` from `configs/team.yaml`

Velocity always comes from the config — Pinya knows nothing about it. Use
`--no-save` to only look without storing, and `--yaml` to also get a
`working_days_per_sprint` block for the `ratio` command.

Requires the optional `pinya` extra — see [Pinya HR setup](#4-pinya-hr-optional).

## Setup

### 1. Team Configuration

Copy and edit the team configuration file:
```bash
$ cp configs/example.yaml configs/team.yaml
```

Edit `configs/team.yaml` with your team details:
```yaml
members:
  - name: "Your Name"
    wd: 10.0   # Work days per sprint (0-10)
    vel: 0.3   # Velocity/productivity factor

  - name: "Teammate Name"
    wd: 4.0    # Part-time member
    vel: 0.6   # Higher productivity
```

**Team Configuration Parameters:**
- **name**: Full name of the team member (must match JIRA assignee names)
- **wd**: Working days in the sprint (0-10, decimals allowed for part-time)
- **vel**: Velocity factor
- **off_days** (optional): Weekdays never worked, e.g. `off_days: [Fr]` for a
  four-day week. Used by the `absences` command: a public holiday on a day the
  member does not work costs them nothing.

Team capacity is calculated as: `wd × vel` for each member.

### 2. Environment Variables

Create a `.env` file in the project root:
```bash
$ cat > .env << EOF
JIRA_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=your.email@company.com
JIRA_TOKEN=your_jira_api_token
PLANNING_FILTER_ID=12345
RATIO_FILTER_ID=67890
PINYA_URL=https://yourcompany.pinya.hr
EOF
```

### 4. Pinya HR (optional)

Only needed for the `absences` command. Pinya has no API access for us, so the
command drives a real browser against the same JSON endpoints its web UI uses.

```bash
$ uv sync --extra pinya
$ uv run playwright install chrome
```

Pinya sits behind Azure SSO with MFA, which cannot be automated. On the first
run a Chrome window opens — log in and tick **"Stay signed in"**. The session is
stored in `~/.jirafly/pinya-profile`, and later runs stay headless until it
expires (weeks, with "Stay signed in").

Required env var: `PINYA_URL` — your Pinya instance, e.g.
`https://yourcompany.pinya.hr`.

**Caveats:**
- The endpoints are undocumented and may change without warning when Pinya is
  updated. An official Pinya API token would make this robust and remove the
  browser entirely.
- Not suitable for unattended cron runs — the session eventually needs a manual
  login.
- Absence type names are whatever your Pinya is configured with, and different
  groups of people may use different types for the same thing. The command
  therefore breaks absences down per type instead of filtering to one — check
  which columns actually appear before trusting a total.
- Company-wide days off that Pinya tracks neither as absences nor holidays (e.g.
  a team offsite) still need a manual correction in `team.yaml`.

### Code Quality

Format and check your code:
```bash
uv run pre-commit run --all-files
```

### Adding dependencies

To add runtime dependencies:

```bash
uv add $PACKAGE_NAME
```

To add development dependencies:

```bash
uv add --dev $PACKAGE_NAME
```

### Configuration

Used environment variables:
- `JIRA_URL` - JIRA server URL (e.g., https://yourcompany.atlassian.net)
- `JIRA_EMAIL` - Email for JIRA authentication
- `JIRA_TOKEN` - JIRA API token for authentication
- `PLANNING_FILTER_ID` - JIRA filter ID for planning command
- `RATIO_FILTER_ID` - JIRA filter ID for ratio analysis
- `PINYA_URL` - Pinya HR instance URL, required by the `absences` command
