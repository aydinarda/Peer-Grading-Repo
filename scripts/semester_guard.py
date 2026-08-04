#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Semester expiry guard.

Decides whether the pipeline is still allowed to compute. Every date it needs
comes from PeerGrading/Input/semester.csv, which is maintained on SharePoint and
delivered by scripts/fetch_mail.py -- nothing semester-dependent is baked into
this repo, because weeks and semester boundaries shift from year to year.

semester.csv is a two-column key/value file:

    key,value
    semester_id,2026-FS
    semester_end,2026-06-14
    tz,Europe/Zurich
    ta_email,ta@uzh.ch
    archive_url,

Optional per-source overrides, for when one input should stop being usable
before the semester itself ends. Anything not listed inherits semester_end:

    expires:forms_responses.xlsx,2026-06-07
    expires:StudentListDB.xlsx,2026-06-14

A date without a time means "end of that day" in tz.

Behaviour once the deadline has passed:
  - No computation happens. The stop is silent by design: no warning window,
    no build noise in the weeks leading up to it.
  - push_outputs.py --mode archive ships the semester bundle, so the Archive the
    closure mail talks about actually exists by the time it is announced.
  - The TA is mailed exactly once, via the dead-drop mailbox, telling them the
    semester is over and their files are headed for the Archive.
  - A marker (PeerGrading/Output/semester_closed.txt) is written and committed
    so later scheduled runs stay quiet instead of re-sending that mail.

Because the guard runs before compute, the archive holds the outputs of the last
successful weekly run - which is what "the last week has been calculated" means.

Exit codes: 0 = decision made (see the `expired` output), 1 = cannot decide, or
the notification could not be delivered.
"""

from __future__ import annotations

import os
import smtplib
import ssl
import subprocess
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from semester_config import SEMESTER_FILE, fail, get_tz, load_semester_config, parse_deadline

MARKER_FILE = Path("PeerGrading/Output/semester_closed.txt")
ARCHIVE_SCRIPT = Path(__file__).resolve().parent / "push_outputs.py"

# Inputs the guard reports on individually, keyed by the name used in
# `expires:<name>` overrides.
TRACKED_SOURCES = {
    "forms_responses.xlsx": Path("PeerGrading/Input/form_exports/forms_responses.xlsx"),
    "StudentListDB.xlsx": Path("PeerGrading/Input/students_db/StudentListDB.xlsx"),
    "week_windows.csv": Path("PeerGrading/Input/week_setup/week_windows.csv"),
}


def build_message(cfg: dict[str, str]) -> str:
    archive_url = cfg.get("archive_url", "").strip()
    archive_line = archive_url if archive_url else "(the archive link will follow separately)"
    return (
        "Hello,\n\n"
        f"The semester ({cfg['semester_id']}) has ended and the last week has been "
        "calculated. No further peer-grading runs will take place.\n\n"
        "Your files -- weekly summaries, peer logs, attendance lists and the "
        "per-presenter mail drafts -- will be stored in the Archive. Please download "
        "anything you still need before they are moved.\n\n"
        f"Archive: {archive_line}\n\n"
        "Best regards,\n"
        "PeerGrading automation\n"
    )


def send_closure_mail(cfg: dict[str, str]) -> None:
    sender = os.getenv("MAIL_SMTP_USER", "").strip() or os.getenv("MAIL_IMAP_USER", "").strip()
    password = os.getenv("MAIL_SMTP_PASSWORD", "").strip() or os.getenv("MAIL_IMAP_PASSWORD", "").strip()
    if not sender or not password:
        fail("cannot send the closure mail: MAIL_IMAP_USER / MAIL_IMAP_PASSWORD are not set.")

    host = os.getenv("MAIL_SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.getenv("MAIL_SMTP_PORT", "465").strip())

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = cfg["ta_email"]
    msg["Subject"] = f"PeerGrading: semester {cfg['semester_id']} has ended"
    msg.set_content(build_message(cfg))

    try:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
    except Exception as exc:  # noqa: BLE001 - surfacing the reason matters more than the type
        fail(f"closure mail to {cfg['ta_email']} could not be sent: {exc}")

    print(f"Closure mail sent to {cfg['ta_email']}.")


def send_semester_archive(cfg: dict[str, str]) -> None:
    """Ship the semester archive before announcing closure.

    Delegates to push_outputs.py so that building and mailing a bundle lives in
    exactly one place. Failing here is fatal on purpose: the marker is only
    written afterwards, so the next scheduled run retries the whole closure
    rather than leaving the TA with a mail pointing at an archive that was never
    produced.
    """
    cmd = [sys.executable, str(ARCHIVE_SCRIPT), "--mode", "archive"]
    print(f"Building semester archive for {cfg['semester_id']}...")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        fail(
            f"semester archive could not be sent (exit {result.returncode}). "
            "Closure is not recorded; the next run will retry."
        )


def write_output(expired: bool) -> None:
    gh_output = os.getenv("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as f:
            f.write(f"expired={'true' if expired else 'false'}\n")


def main() -> None:
    cfg = load_semester_config(SEMESTER_FILE)
    tz = get_tz(cfg)
    now = datetime.now(tz)
    semester_end = parse_deadline(cfg["semester_end"], tz, "semester_end")

    # Per-source expiry, defaulting to the semester deadline.
    expired_sources = []
    for name, path in TRACKED_SOURCES.items():
        override = cfg.get(f"expires:{name}", "").strip()
        deadline = parse_deadline(override, tz, f"expires:{name}") if override else semester_end
        if now > deadline:
            expired_sources.append((name, deadline))
        elif not path.exists():
            print(f"NOTE: {name} is not on disk at {path}.")

    semester_over = now > semester_end
    expired = semester_over or bool(expired_sources)

    if not expired:
        print(
            f"Semester {cfg['semester_id']} is active "
            f"(now {now:%Y-%m-%d %H:%M %Z}, ends {semester_end:%Y-%m-%d %H:%M %Z})."
        )
        write_output(False)
        return

    for name, deadline in expired_sources:
        print(f"Expired input: {name} (valid until {deadline:%Y-%m-%d %H:%M %Z}).")
    if semester_over:
        print(f"Semester {cfg['semester_id']} ended {semester_end:%Y-%m-%d %H:%M %Z} - not computing.")
    else:
        print(f"Semester {cfg['semester_id']} runs until {semester_end:%Y-%m-%d %H:%M %Z}, "
              "but an input has expired - not computing.")

    already_closed = MARKER_FILE.exists() and cfg["semester_id"] in MARKER_FILE.read_text(encoding="utf-8")
    if already_closed:
        print("Closure was already announced - staying silent.")
        write_output(True)
        return

    send_semester_archive(cfg)
    send_closure_mail(cfg)
    MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    MARKER_FILE.write_text(
        f"semester_id={cfg['semester_id']}\n"
        f"semester_end={semester_end.isoformat()}\n"
        f"closed_at={now.isoformat()}\n"
        f"notified={cfg['ta_email']}\n",
        encoding="utf-8",
    )
    print(f"Wrote {MARKER_FILE}.")
    write_output(True)


if __name__ == "__main__":
    main()
