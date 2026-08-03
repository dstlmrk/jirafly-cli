"""Pinya HR client for absence data.

Pinya gives us no API access, so this talks to the same JSON endpoints its own
web UI uses (Kendo grids: form-encoded POST, server-side paging, no antiforgery
token). A real browser carries the session because Pinya sits behind Azure SSO
with MFA, which cannot be automated: on the first run a Chrome window opens for
the login, afterwards the stored profile keeps the session and the browser stays
headless.

Working days come out of the per-day "Units" breakdown of each record, where
weekends and public holidays already have Amount = 0, so booked hours divided by
the employee's daily shift gives working days - including half days and
part-time contracts.

Requires the optional "pinya" extra: uv sync --extra pinya && playwright install chrome
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DEFAULT_PROFILE_DIR = Path.home() / ".jirafly" / "pinya-profile"

LOGIN_TIMEOUT_MS = 15 * 60 * 1000
PAGE_SIZE = 500
DEFAULT_DAY_SHIFT = 8.0
REJECTED_STATE = 3
HOLIDAY_EVENT_TYPE = 20
# Calendar event types the UI requests; needed to get holidays back.
CALENDAR_EVENT_TYPE_IDS = (0, 2, 4, 6, 8, 10, 12)
# "Česká republika" in the public holidays picker.
CZ_HOLIDAY_CALENDAR_ID = "323754"


class PinyaError(RuntimeError):
    pass


@dataclass
class AbsenceRecord:
    employee_id: int
    employee_name: str
    day_shift: float
    type_name: str
    state: int
    term: str
    # Per-day breakdown from Pinya; weekends and holidays already have
    # Amount = 0. Counting these days rather than whole records is what makes an
    # absence spanning a sprint boundary land in the right sprint.
    units: list[dict] = field(default_factory=list)


class PinyaClient:
    """Reads absences and public holidays from Pinya HR."""

    def __init__(
        self,
        base_url: str,
        profile_dir: Path = DEFAULT_PROFILE_DIR,
    ):
        if not base_url:
            raise PinyaError("Pinya URL must be provided (set PINYA_URL)")
        self.base_url = base_url.rstrip("/")
        self.profile_dir = profile_dir
        self._playwright = None
        self._context = None
        self.me_id: int | None = None

    # -- lifecycle --------------------------------------------------------- #

    def __enter__(self) -> "PinyaClient":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise PinyaError(
                "Playwright is required for Pinya access. Install it with:\n"
                "  uv sync --extra pinya && uv run playwright install chrome"
            ) from exc

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._open_context(headless=True)

        if not self._logged_in():
            print("Pinya session expired, opening browser for login...")
            self._close_context()
            self._open_context(headless=False)
            self._wait_for_login()

        print("Pinya session established")
        return self

    def __exit__(self, *exc) -> None:
        self._close_context()
        if self._playwright:
            self._playwright.stop()

    def _open_context(self, headless: bool) -> None:
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            channel="chrome",
            headless=headless,
            viewport={"width": 1500, "height": 950},
        )

    def _close_context(self) -> None:
        if self._context:
            self._context.close()
            self._context = None

    def _page(self):
        return (
            self._context.pages[0] if self._context.pages else self._context.new_page()
        )

    def _logged_in(self) -> bool:
        page = self._page()
        page.goto(f"{self.base_url}/Team", wait_until="domcontentloaded")
        return self._is_app_url(page.url)

    def _is_app_url(self, url: str) -> bool:
        return (
            url.startswith(self.base_url)
            and "/Login" not in url
            and "/Account/" not in url
        )

    def _wait_for_login(self) -> None:
        from playwright.sync_api import TimeoutError as PWTimeout

        page = self._page()
        page.goto(f"{self.base_url}/Team", wait_until="domcontentloaded")
        try:
            page.bring_to_front()
        except Exception:
            pass
        print(
            "  Log in with Microsoft SSO in the open Chrome window.\n"
            "  Tick 'Stay signed in' so this is not needed next time.\n"
            f"  Waiting up to {LOGIN_TIMEOUT_MS // 60000} minutes..."
        )
        try:
            page.wait_for_url(self._is_app_url, timeout=LOGIN_TIMEOUT_MS)
        except PWTimeout:
            raise PinyaError("Pinya login was not completed in time") from None

    # -- transport --------------------------------------------------------- #

    def _post(self, path: str, form: dict) -> dict:
        response = self._context.request.post(
            f"{self.base_url}{path}",
            form=form,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": f"{self.base_url}/Team",
            },
        )
        if response.status != 200:
            raise PinyaError(f"POST {path} returned HTTP {response.status}")
        try:
            payload = response.json()
        except Exception as exc:
            raise PinyaError(f"POST {path} did not return JSON: {exc}") from exc
        if payload.get("Errors"):
            raise PinyaError(f"POST {path} reported errors: {payload['Errors']}")
        return payload

    # -- domain ------------------------------------------------------------ #

    def team(self, include_indirect: bool = False) -> dict[int, str]:
        """Team roster as {employee_id: name}; also resolves own employee id.

        Pinya has no "who am I" endpoint, so the own id is the one that appears
        only when includeMe is on.
        """

        def roster(with_me: bool) -> dict[int, str]:
            payload = self._post(
                "/Team/GetJson",
                {
                    "sort": "",
                    "group": "",
                    "filter": "",
                    "managerId": "",
                    "includeHidden": "false",
                    "includeIndirect": "true" if include_indirect else "false",
                    "includeMe": "true" if with_me else "false",
                },
            )
            return {
                int(row["Id"]): (row.get("Name") or "?").strip()
                for row in payload.get("Data") or []
            }

        with_me = roster(True)
        extra = set(with_me) - set(roster(False))
        self.me_id = extra.pop() if len(extra) == 1 else None
        return with_me

    def _grid(self, extra: dict) -> list[AbsenceRecord]:
        records: list[AbsenceRecord] = []
        page_no = 1
        while True:
            payload = self._post(
                "/HR/Absence/EmployeeGrid_Read",
                {
                    "sort": "",
                    "page": str(page_no),
                    "pageSize": str(PAGE_SIZE),
                    "group": "",
                    "aggregate": "",
                    "filter": "",
                    "display": "0",  # all records
                    "includeHidden": "false",
                    **extra,
                },
            )
            rows = payload.get("Data") or []
            records.extend(
                AbsenceRecord(
                    employee_id=int(row.get("EmployeeId") or 0),
                    employee_name=(row.get("EmployeeName") or "").strip(),
                    day_shift=float(row.get("EmployeeDayWorkingShift") or 0),
                    type_name=(row.get("DisplayType") or "?").strip(),
                    state=int(row.get("State") or 0),
                    term=row.get("Term") or "",
                    units=row.get("Units") or [],
                )
                for row in rows
            )
            if len(records) >= int(payload.get("Total") or 0) or not rows:
                break
            page_no += 1
        return records

    def absence_records(self, include_indirect: bool = False) -> list[AbsenceRecord]:
        """Absence records for the team plus my own.

        The grid ignores includeMe and only returns subordinates, so own records
        need a separate call scoped by employeeId - which also keeps other
        people's data out of the response.
        """
        print("Fetching absences...", end=" ")
        records = self._grid(
            {
                "managerId": str(self.me_id or ""),
                "includeMe": "false",
                "includeIndirect": "true" if include_indirect else "false",
            }
        )
        if self.me_id:
            records += self._grid({"managerId": "", "employeeId": str(self.me_id)})
        print(f"total records downloaded: {len(records)}")
        return records

    def public_holidays(self, since: date, until: date) -> list[tuple[date, str]]:
        """Public holidays in the period, as (day, name).

        Pinya zeroes them out in absence Units, so they never show up as
        absences - but they still reduce everyone's working days.
        """
        payload = self._post(
            "/Calendar/Team",
            {
                "sort": "",
                "group": "",
                "filter": "",
                "start": f"{since.isoformat()}T00:00:00.000Z",
                "end": f"{until.isoformat()}T23:59:59.000Z",
                **{
                    f"eventTypeIds[{i}]": str(v)
                    for i, v in enumerate(CALENDAR_EVENT_TYPE_IDS)
                },
                "publicHolidays[0]": CZ_HOLIDAY_CALENDAR_ID,
                "managerId": "",
                "includeMe": "true",
                "includeHidden": "false",
                "includeIndirect": "false",
            },
        )
        holidays = []
        for row in payload.get("Data") or []:
            if row.get("Type") != HOLIDAY_EVENT_TYPE:
                continue
            raw = row.get("Start")
            if not raw:
                continue
            try:
                day = date.fromisoformat(raw[:10])
            except ValueError:
                continue
            if since <= day <= until:
                holidays.append((day, (row.get("Title") or "").strip()))
        return sorted(set(holidays))


def daily_shifts(
    records: list[AbsenceRecord], employee_ids: set[int]
) -> dict[int, float]:
    """Hours of a full working day, per employee.

    Pinya reports EmployeeDayWorkingShift for subordinates but sends 0 for our
    own records, so fall back to the longest single day in the data - a full day
    is by definition the longest, which holds for part-timers too.
    """
    reported: dict[int, float] = {}
    longest: dict[int, float] = defaultdict(float)

    for record in records:
        if record.employee_id not in employee_ids:
            continue
        if record.day_shift > 0:
            reported.setdefault(record.employee_id, record.day_shift)
        for unit in record.units:
            amount = float(unit.get("Amount") or 0)
            longest[record.employee_id] = max(longest[record.employee_id], amount)

    return {
        employee_id: reported.get(employee_id)
        or longest.get(employee_id)
        or DEFAULT_DAY_SHIFT
        for employee_id in employee_ids
    }


@dataclass
class AbsenceBreakdown:
    """Absence days per member, both summed per type and spread over days."""

    by_type: dict[str, dict[str, float]] = field(default_factory=dict)
    # member -> ISO date -> absence type -> days
    by_day: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    rejected: list[AbsenceRecord] = field(default_factory=list)


def absence_days_by_member(
    records: list[AbsenceRecord],
    roster: dict[int, str],
    since: date,
    until: date,
) -> AbsenceBreakdown:
    """Working days of absence per member, per type and per day.

    The per-day view keeps which days are actually taken off, which the summed
    totals cannot express - needed to draw a sprint calendar.
    """
    shifts = daily_shifts(records, set(roster))
    result = AbsenceBreakdown(
        by_type={name: {} for name in roster.values()},
        by_day={name: {} for name in roster.values()},
    )

    for record in records:
        if record.employee_id not in roster:
            continue
        name = record.employee_name or roster[record.employee_id]
        if record.state == REJECTED_STATE:
            record.employee_name = name
            result.rejected.append(record)
            continue

        shift = shifts[record.employee_id]
        per_type = result.by_type.setdefault(name, {})
        per_day = result.by_day.setdefault(name, {})

        for unit in record.units:
            raw = unit.get("Date")
            if not raw:
                continue
            try:
                day = date.fromisoformat(raw[:10])
            except ValueError:
                continue
            if not (since <= day <= until):
                continue
            hours = float(unit.get("Amount") or 0)
            if hours <= 0:  # weekend or public holiday
                continue
            days = hours / shift
            per_type[record.type_name] = per_type.get(record.type_name, 0.0) + days
            day_entry = per_day.setdefault(day.isoformat(), {})
            day_entry[record.type_name] = day_entry.get(record.type_name, 0.0) + days

    return result
