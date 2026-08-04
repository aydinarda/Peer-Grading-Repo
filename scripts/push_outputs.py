#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ship pipeline outputs back to SharePoint, via the mail dead-drop.

The mirror image of scripts/fetch_mail.py. A second Power Automate flow watches
the outbox mailbox and writes the attached zip's contents into SharePoint, which
keeps the whole round trip on the same transport - no share links to re-issue,
no app registration to get approved.

Modes:
  --mode weekly   the current PeerGrading/Output tree (weekly CSVs, master/,
                  mails/, drafts/, weekly_status.csv).
                  Subject: "PeerGrading Outputs <semester_id>"
  --mode archive  everything the semester produced: a snapshot of the inputs,
                  the full output tree, and a MANIFEST.txt.
                  Subject: "PeerGrading Archive <semester_id>"
                  Power Automate files these under Archive/<semester_id>/.

Required env vars:
  MAIL_OUTBOX_TO       address the Power Automate flow watches
  MAIL_IMAP_USER       sender mailbox (reused for SMTP)
  MAIL_IMAP_PASSWORD   app password for that mailbox

Optional env vars:
  MAIL_SMTP_HOST       default: smtp.gmail.com
  MAIL_SMTP_PORT       default: 465
  MAIL_SMTP_USER       overrides MAIL_IMAP_USER
  MAIL_SMTP_PASSWORD   overrides MAIL_IMAP_PASSWORD

--dry-run writes the zip next to the outputs instead of mailing it, so the
bundle can be inspected without touching a mailbox.
"""

from __future__ import annotations

import argparse
import os
import smtplib
import ssl
import tempfile
import zipfile
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from semester_config import fail, get_tz, load_semester_config

OUT_DIR = Path("PeerGrading/Output")
INPUT_FILES = [
    Path("PeerGrading/Input/form_exports/forms_responses.xlsx"),
    Path("PeerGrading/Input/students_db/StudentListDB.xlsx"),
    Path("PeerGrading/Input/week_setup/week_windows.csv"),
    Path("PeerGrading/Input/semester.csv"),
]
DEFAULT_MAX_MB = 20


def collect_output_files(out_dir: Path) -> list[Path]:
    if not out_dir.exists():
        fail(f"{out_dir} does not exist - nothing to push. Has compute run?")
    return sorted(p for p in out_dir.rglob("*") if p.is_file())


def build_manifest(cfg: dict[str, str], files: list[Path], generated_at: datetime) -> str:
    lines = [
        f"semester_id: {cfg['semester_id']}",
        f"semester_end: {cfg['semester_end']}",
        f"generated_at: {generated_at.isoformat()}",
        f"file_count: {len(files)}",
        "",
    ]

    status_file = OUT_DIR / "weekly_status.csv"
    if status_file.exists():
        lines.append("Week-by-week status (from weekly_status.csv):")
        lines.append(status_file.read_text(encoding="utf-8").strip())
        lines.append("")

    lines.append("Files:")
    lines.extend(f"  {p.as_posix()}" for p in files)
    lines.append("")
    return "\n".join(lines)


def build_zip(mode: str, cfg: dict[str, str], zip_path: Path, generated_at: datetime) -> list[Path]:
    output_files = collect_output_files(OUT_DIR)
    included = list(output_files)

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in output_files:
            zf.write(path, arcname=path.as_posix())

        if mode == "archive":
            for path in INPUT_FILES:
                if path.exists():
                    zf.write(path, arcname=path.as_posix())
                    included.append(path)
                else:
                    print(f"NOTE: {path} is not on disk; archiving without it.")
            zf.writestr("MANIFEST.txt", build_manifest(cfg, included, generated_at))

    return included


def send_bundle(subject: str, body: str, zip_path: Path) -> None:
    recipient = os.getenv("MAIL_OUTBOX_TO", "").strip()
    sender = os.getenv("MAIL_SMTP_USER", "").strip() or os.getenv("MAIL_IMAP_USER", "").strip()
    password = os.getenv("MAIL_SMTP_PASSWORD", "").strip() or os.getenv("MAIL_IMAP_PASSWORD", "").strip()
    if not sender or not password:
        fail("cannot send the bundle: MAIL_IMAP_USER / MAIL_IMAP_PASSWORD are not set.")

    host = os.getenv("MAIL_SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.getenv("MAIL_SMTP_PORT", "465").strip())

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(
        zip_path.read_bytes(),
        maintype="application",
        subtype="zip",
        filename=zip_path.name,
    )

    try:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
    except Exception as exc:  # noqa: BLE001 - the reason matters more than the type
        fail(f"bundle could not be sent to {recipient}: {exc}")

    print(f"Sent '{subject}' to {recipient} ({zip_path.stat().st_size} bytes).")


def main() -> None:
    ap = argparse.ArgumentParser(description="Mail pipeline outputs to the SharePoint sync mailbox.")
    ap.add_argument("--mode", choices=["weekly", "archive"], required=True)
    ap.add_argument("--dry-run", action="store_true", help="Write the zip locally instead of mailing it.")
    ap.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB,
                    help=f"Refuse to send a bundle larger than this (default {DEFAULT_MAX_MB}).")
    args = ap.parse_args()

    # Checked before any work: no point zipping a bundle we cannot deliver.
    if not args.dry_run and not os.getenv("MAIL_OUTBOX_TO", "").strip():
        fail("MAIL_OUTBOX_TO is not set - there is nowhere to send the bundle.")

    cfg = load_semester_config()
    generated_at = datetime.now(get_tz(cfg))
    semester_id = cfg["semester_id"]

    stamp = generated_at.strftime("%Y%m%d-%H%M")
    if args.mode == "archive":
        zip_name = f"PeerGrading-Archive-{semester_id}.zip"
        subject = f"PeerGrading Archive {semester_id}"
        body = (
            f"Semester {semester_id} archive, generated {generated_at.isoformat()}.\n"
            "Contains the input snapshot, every generated output, the per-week mail\n"
            "drafts and a MANIFEST.txt.\n"
        )
    else:
        zip_name = f"PeerGrading-Outputs-{semester_id}-{stamp}.zip"
        subject = f"PeerGrading Outputs {semester_id}"
        body = f"Weekly outputs for {semester_id}, generated {generated_at.isoformat()}.\n"

    # Built outside the working tree by default, so a half-finished bundle can
    # never show up in git status and end up committed.
    zip_path = Path(os.getenv("PUSH_ZIP_DIR", tempfile.gettempdir())) / zip_name
    included = build_zip(args.mode, cfg, zip_path, generated_at)

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"Built {zip_path} ({size_mb:.2f} MB, {len(included)} files).")
    if size_mb > args.max_mb:
        fail(
            f"bundle is {size_mb:.2f} MB, over the {args.max_mb} MB limit. "
            "Refusing to send rather than have the mail silently rejected."
        )

    if args.dry_run:
        print("Dry run - not sending. Zip left on disk for inspection.")
        return

    send_bundle(subject, body, zip_path)
    zip_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
