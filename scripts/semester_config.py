#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared reader for PeerGrading/Input/semester.csv.

The file is maintained on SharePoint and delivered by scripts/fetch_mail.py.
It is a two-column key/value CSV:

    key,value
    semester_id,2026-FS
    semester_end,2026-06-14
    tz,Europe/Zurich
    ta_email,ta@uzh.ch
    archive_url,

Optional per-source overrides, for inputs that should stop being usable before
the semester itself ends. Anything not listed inherits semester_end:

    expires:forms_responses.xlsx,2026-06-07

Used by semester_guard.py, compute_weekly.py and push_outputs.py so the same
dates are parsed the same way everywhere.
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SEMESTER_FILE = Path("PeerGrading/Input/semester.csv")
DEFAULT_TZ = "Europe/Zurich"
REQUIRED_KEYS = ("semester_id", "semester_end", "ta_email")


def fail(msg: str) -> None:
    print(f"ERROR: {msg}")
    sys.exit(1)


def load_semester_config(path: Path = SEMESTER_FILE) -> dict[str, str]:
    if not path.exists():
        fail(
            f"{path} not found. It is delivered from SharePoint by the mail sync; "
            "without it there is no expiry date, and the pipeline refuses to run "
            "against data it cannot date-check."
        )

    cfg: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip() or row[0].lstrip().startswith("#"):
                continue
            key = row[0].strip()
            value = row[1].strip() if len(row) > 1 else ""
            if key.lower() in {"key", "setting"} and value.lower() in {"value", ""}:
                continue  # header row
            cfg[key] = value

    for required in REQUIRED_KEYS:
        if not cfg.get(required):
            fail(f"{path} is missing a value for '{required}'.")
    return cfg


def get_tz(cfg: dict[str, str]) -> ZoneInfo:
    tz_name = cfg.get("tz", "").strip() or DEFAULT_TZ
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        fail(f"unknown timezone '{tz_name}' in {SEMESTER_FILE}.")
        raise  # unreachable, keeps type checkers happy


def parse_deadline(value: str, tz: ZoneInfo, label: str) -> datetime:
    """Parse a date or datetime. A bare date means the whole day is still valid."""
    raw = value.strip().replace("/", "-")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        fail(f"could not parse {label} '{value}'. Use YYYY-MM-DD or YYYY-MM-DD HH:MM.")
        raise  # unreachable
    if len(raw) <= 10:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)
