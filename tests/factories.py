"""Builders for synthetic PeerGrading data folders.

The shapes here mirror the real Forms export, including the awkward parts that
actually occur in it: a `utcNow()` literal that never got resolved, a ResponseId
reused across different responses, presenter names separated by tabs or
newlines, non-ASCII names, `Q_4` instead of `Q4`, and `Q3_3` appearing before
`Q3_2`. The names and addresses are invented - real course data never enters
this repo.

Week windows are generated relative to today, so "past", "current" and "future"
stay true whenever the suite runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

TZ = "Europe/Zurich"


@dataclass(frozen=True)
class Student:
    first: str
    last: str
    email: str

    @property
    def full(self) -> str:
        return f"{self.first} {self.last}"


# A small class with the same awkward cases as the real roster: a non-ASCII
# name, a two-word surname, and a two-word first name.
ROSTER = [
    Student("Ana", "Lang", "ana.lang@example.edu"),
    Student("Björn", "Sjöberg", "bjorn.sjoberg@example.edu"),
    Student("Carlos", "Diaz Ruiz", "carlos.diazruiz@example.edu"),
    Student("Dana", "Okoro", "dana.okoro@example.edu"),
    Student("Emil", "Novak", "emil.novak@example.edu"),
    Student("Farida", "Haddad", "farida.haddad@example.edu"),
    Student("Mei Ling", "Chen", "meiling.chen@example.edu"),
    Student("Hugo", "Silva", "hugo.silva@example.edu"),
    Student("Irina", "Petrova", "irina.petrova@example.edu"),
    Student("Jonas", "Meyer", "jonas.meyer@example.edu"),
]

BY_LAST = {s.last: s for s in ROSTER}

LIKERT = {1: "Bad 1", 2: "Poor 2", 3: "Fair 3", 4: "Good 4", 5: "Excellent 5"}


@dataclass
class Response:
    """One row of the Forms export."""
    response_id: int
    week: str
    rater: Student
    presenter: str          # written as it appears in the dropdown, separators and all
    scores: dict[str, int]  # Q2_1..Q3_3, Q4
    comment: str = ""
    hour_offset: int = 12   # position inside the week, in hours from its start
    raw_received: str | None = None   # overrides the computed timestamp
    submitted_at: str | None = None


def presenter_choice(student: Student, sep: str = "\t") -> str:
    """Mimic the dropdown value: first and last name joined by an odd separator."""
    return f"{student.first}{sep}{student.last}"


def build_week_windows(n_past: int = 4, n_future: int = 2) -> list[dict]:
    """Weeks W01.. anchored on today, so past/future never drifts.

    The last past week ends yesterday and the first future week starts tomorrow,
    leaving an unambiguous gap around "now".
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows: list[dict] = []

    last_past_end = today - timedelta(days=1)
    for i in range(n_past):
        end = last_past_end - timedelta(days=7 * (n_past - 1 - i))
        start = end - timedelta(days=7)
        rows.append(_window(f"W{i + 1:02d}", start, end))

    first_future_start = today + timedelta(days=1)
    for j in range(n_future):
        start = first_future_start + timedelta(days=7 * j)
        rows.append(_window(f"W{n_past + j + 1:02d}", start, start + timedelta(days=7)))

    return rows


def _window(week_id: str, start: datetime, end: datetime) -> dict:
    fmt = "%Y-%m-%d %H:%M"
    return {
        "week_id": week_id,
        "start_local": start.strftime(fmt),
        "end_local": end.strftime(fmt),
        "deadline_local": end.strftime(fmt),
        "tz": TZ,
    }


def received_utc(windows: list[dict], week_id: str, hour_offset: int = 12) -> str:
    """An ISO-8601 UTC stamp that lands inside `week_id` once converted to local."""
    window = next(w for w in windows if w["week_id"] == week_id)
    local = datetime.strptime(window["start_local"], "%Y-%m-%d %H:%M")
    local = local.replace(tzinfo=ZoneInfo(TZ)) + timedelta(hours=hour_offset)
    return local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.%f0Z")


def write_roster(path: Path, roster: list[Student] = None) -> None:
    roster = roster or ROSTER
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {
            "Username": s.email.split("@")[0],
            "First name": s.first,
            "Last name": s.last,
            "E-mail": s.email,
            "Roles": "Group member",
        }
        for s in roster
    ]).to_excel(path, index=False)


def write_week_windows(path: Path, windows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(windows).to_csv(path, index=False)


# Column order as the real export has it: Q3_3 before Q3_2, and Q_4 not Q4.
FORMS_COLUMNS = [
    "ResponseId", "SubmittedAt", "RaterEmail", "RaterName", "PresenterChoice",
    "Q2_1", "Q2_2", "Q2_3", "Q3_1", "Q3_3", "Q3_2", "Q_4", "Comment", "ReceivedAtUTC",
]


def write_forms(path: Path, responses: list[Response], windows: list[dict]) -> None:
    """Write the export, preserving the real file's column quirks.

    An export with no responses still carries its header row, which is what
    Forms actually produces before anyone has submitted anything.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in responses:
        received = r.raw_received if r.raw_received is not None else received_utc(
            windows, r.week, r.hour_offset
        )
        rows.append({
            "ResponseId": r.response_id,
            "SubmittedAt": r.submitted_at,
            "RaterEmail": r.rater.email,
            "RaterName": r.rater.full,
            "PresenterChoice": r.presenter,
            "Q2_1": LIKERT[r.scores["Q2_1"]],
            "Q2_2": LIKERT[r.scores["Q2_2"]],
            "Q2_3": LIKERT[r.scores["Q2_3"]],
            "Q3_1": LIKERT[r.scores["Q3_1"]],
            # Deliberately out of order, exactly as the real export has it.
            "Q3_3": LIKERT[r.scores["Q3_3"]],
            "Q3_2": LIKERT[r.scores["Q3_2"]],
            "Q_4": LIKERT[r.scores["Q4"]],
            "Comment": r.comment,
            "ReceivedAtUTC": received,
        })
    pd.DataFrame(rows, columns=FORMS_COLUMNS).to_excel(path, index=False)


def write_semester(path: Path, semester_end: str, semester_id: str = "2026-TEST",
                   ta_email: str = "ta@example.edu", extra: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ("key", "value"),
        ("semester_id", semester_id),
        ("semester_end", semester_end),
        ("tz", TZ),
        ("ta_email", ta_email),
        ("archive_url", ""),
    ]
    rows.extend((k, v) for k, v in (extra or {}).items())
    pd.DataFrame(rows[1:], columns=list(rows[0])).to_csv(path, index=False)


def uniform(value: int) -> dict[str, int]:
    return {k: value for k in ("Q2_1", "Q2_2", "Q2_3", "Q3_1", "Q3_2", "Q3_3", "Q4")}


def scores(q2_1, q2_2, q2_3, q3_1, q3_2, q3_3, q4) -> dict[str, int]:
    return {
        "Q2_1": q2_1, "Q2_2": q2_2, "Q2_3": q2_3,
        "Q3_1": q3_1, "Q3_2": q3_2, "Q3_3": q3_3, "Q4": q4,
    }


@dataclass
class DataRoot:
    """A complete PeerGrading folder on disk."""
    path: Path
    windows: list[dict] = field(default_factory=list)

    @property
    def inputs(self) -> Path:
        return self.path / "Input"

    @property
    def outputs(self) -> Path:
        return self.path / "Output"

    @property
    def archive(self) -> Path:
        return self.path / "Archive"

    @property
    def forms(self) -> Path:
        return self.inputs / "form_exports" / "forms_responses.xlsx"

    def week(self, week_id: str) -> Path:
        """The folder holding everything produced for one week."""
        return self.outputs / week_id

    def drafts(self, week_id: str) -> Path:
        """The .eml files, inside the week's folder and apart from the .txt copies."""
        return self.week(week_id) / "emls"

    def mails(self, week_id: str) -> Path:
        return self.week(week_id) / "mails"

    def rewrite_forms(self, responses: list[Response]) -> None:
        write_forms(self.forms, responses, self.windows)


def make_root(base: Path, responses: list[Response], *, n_past: int = 4,
              n_future: int = 2, semester_end: str | None = None,
              semester_extra: dict | None = None,
              roster: list[Student] = None) -> DataRoot:
    """Assemble a data folder ready for compute_weekly.py --root."""
    base.mkdir(parents=True, exist_ok=True)
    windows = build_week_windows(n_past, n_future)

    if semester_end is None:
        # Well past the last generated week, so the guard stays out of the way.
        last_end = datetime.strptime(windows[-1]["end_local"], "%Y-%m-%d %H:%M")
        semester_end = (last_end + timedelta(days=30)).strftime("%Y-%m-%d")

    write_roster(base / "Input" / "students_db" / "StudentListDB.xlsx", roster)
    write_week_windows(base / "Input" / "week_setup" / "week_windows.csv", windows)
    write_forms(base / "Input" / "form_exports" / "forms_responses.xlsx", responses, windows)
    write_semester(base / "Input" / "semester.csv", semester_end, extra=semester_extra)

    return DataRoot(path=base, windows=windows)
