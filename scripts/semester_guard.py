#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Semester expiry guard.

Decides whether the pipeline is still allowed to compute. Every date it needs
comes from semester.csv in the SharePoint folder -- nothing semester-dependent is
baked into this repo, because weeks and semester boundaries shift year to year.

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

A date without a time means "end of that day" in tz.

Behaviour once the deadline has passed:
  - No computation happens. The stop is silent by design: no warning window,
    no noise in the weeks leading up to it.
  - Everything the semester produced is copied into Archive/<semester_id>/,
    alongside a snapshot of the inputs and a MANIFEST.txt.
  - A closure notice is written there, and a draft mail to the TA is generated
    as an .eml -- the same drag-into-Outlook flow as the student feedback mails,
    so the system needs no mail credentials of its own.
  - A marker (Output/semester_closed.txt) records that this happened, so later
    runs stay quiet instead of redoing it.

Because the guard runs before compute, the archive holds the outputs of the last
successful weekly run - which is what "the last week has been calculated" means.

Exit codes, for the run wrappers to act on:
  0  semester is active - go ahead and compute
  2  expired - stop, do not compute (archiving, if due, has already happened)
  1  could not decide, or the archive could not be written
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

import paths
from semester_config import fail, get_tz, load_semester_config, parse_deadline

EXIT_EXPIRED = 2


def build_message(cfg: dict[str, str], archive_dir: Path) -> str:
    archive_url = cfg.get("archive_url", "").strip()
    archive_line = archive_url if archive_url else archive_dir.as_posix()
    return (
        "Hello,\n\n"
        f"The semester ({cfg['semester_id']}) has ended and the last week has been "
        "calculated. No further peer-grading runs will take place.\n\n"
        "Your files -- weekly summaries, peer logs, attendance lists and the "
        "per-presenter mail drafts -- have been stored in the Archive. Please download "
        "anything you still need before they are moved.\n\n"
        f"Archive: {archive_line}\n\n"
        "Best regards,\n"
        "PeerGrading automation\n"
    )


def build_manifest(cfg: dict[str, str], files: list[Path], generated_at: datetime) -> str:
    lines = [
        f"semester_id: {cfg['semester_id']}",
        f"semester_end: {cfg['semester_end']}",
        f"generated_at: {generated_at.isoformat()}",
        f"file_count: {len(files)}",
        "",
    ]

    status_file = paths.outputs() / "weekly_status.csv"
    if status_file.exists():
        lines.append("Week-by-week status (from weekly_status.csv):")
        lines.append(status_file.read_text(encoding="utf-8").strip())
        lines.append("")

    lines.append("Files:")
    lines.extend(f"  {p}" for p in files)
    lines.append("")
    return "\n".join(lines)


def build_archive(cfg: dict[str, str], generated_at: datetime) -> Path:
    """Copy everything the semester produced into Archive/<semester_id>/.

    Failing here is fatal on purpose: the marker is only written afterwards, so
    the next run retries the whole closure rather than leaving behind a notice
    pointing at an archive that was never produced.
    """
    archive_dir = paths.archive() / cfg["semester_id"]
    print(f"Archiving semester {cfg['semester_id']} to {archive_dir} ...")

    try:
        archive_dir.mkdir(parents=True, exist_ok=True)

        out_dir = paths.outputs()
        copied: list[Path] = []
        if out_dir.is_dir():
            dest = archive_dir / "Output"
            shutil.copytree(out_dir, dest, dirs_exist_ok=True)
            copied.extend(sorted(p.relative_to(archive_dir) for p in dest.rglob("*") if p.is_file()))
        else:
            print(f"NOTE: {out_dir} does not exist; archiving inputs only.")

        # The whole Input tree, so the rubric and mail template are recorded
        # alongside the data they produced results from.
        in_dir = paths.inputs()
        if in_dir.is_dir():
            dest = archive_dir / "Input"
            shutil.copytree(in_dir, dest, dirs_exist_ok=True)
            copied.extend(sorted(p.relative_to(archive_dir) for p in dest.rglob("*") if p.is_file()))
        else:
            print(f"NOTE: {in_dir} does not exist; archiving outputs only.")

        (archive_dir / "MANIFEST.txt").write_text(
            build_manifest(cfg, sorted(copied), generated_at), encoding="utf-8"
        )
    except OSError as exc:
        fail(
            f"could not write the archive to {archive_dir}: {exc}. "
            "Closure is not recorded; the next run will retry."
        )

    print(f"Archived {len(copied)} file(s).")
    return archive_dir


def write_closure_notice(cfg: dict[str, str], archive_dir: Path, generated_at: datetime) -> None:
    """Leave the closure message where the next person will find it.

    Two forms: a plain notice inside the archive, and an .eml draft addressed to
    the TA, so whoever runs this can drag it into Outlook and send it if they are
    not the TA themselves.
    """
    body = build_message(cfg, archive_dir)
    subject = f"PeerGrading: semester {cfg['semester_id']} has ended"

    (archive_dir / "SEMESTER_CLOSED.txt").write_text(
        f"{subject}\n\n{body}", encoding="utf-8"
    )

    msg = EmailMessage()
    msg["To"] = cfg["ta_email"]
    msg["Subject"] = subject
    msg["Date"] = format_datetime(generated_at)
    msg.set_content(body)
    draft = archive_dir / "semester_closed.eml"
    draft.write_bytes(bytes(msg))

    print(f"Closure notice written to {archive_dir / 'SEMESTER_CLOSED.txt'}")
    print(f"Draft mail for {cfg['ta_email']}: {draft}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Stop the pipeline once the semester has ended.")
    paths.add_root_argument(ap)
    args = ap.parse_args()
    paths.set_root(args.root)

    cfg = load_semester_config()
    tz = get_tz(cfg)
    now = datetime.now(tz)
    semester_end = parse_deadline(cfg["semester_end"], tz, "semester_end")

    # Per-source expiry, defaulting to the semester deadline.
    expired_sources = []
    for name, path in paths.tracked_inputs().items():
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
        return

    for name, deadline in expired_sources:
        print(f"Expired input: {name} (valid until {deadline:%Y-%m-%d %H:%M %Z}).")
    if semester_over:
        print(f"Semester {cfg['semester_id']} ended {semester_end:%Y-%m-%d %H:%M %Z} - not computing.")
    else:
        print(f"Semester {cfg['semester_id']} runs until {semester_end:%Y-%m-%d %H:%M %Z}, "
              "but an input has expired - not computing.")

    marker = paths.marker_file()
    already_closed = marker.exists() and cfg["semester_id"] in marker.read_text(encoding="utf-8")
    if already_closed:
        print("Closure was already recorded - staying silent.")
        sys.exit(EXIT_EXPIRED)

    archive_dir = build_archive(cfg, now)
    write_closure_notice(cfg, archive_dir, now)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        f"semester_id={cfg['semester_id']}\n"
        f"semester_end={semester_end.isoformat()}\n"
        f"closed_at={now.isoformat()}\n"
        f"notified={cfg['ta_email']}\n",
        encoding="utf-8",
    )
    print(f"Wrote {marker}.")
    sys.exit(EXIT_EXPIRED)


if __name__ == "__main__":
    main()
