#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[2nd pipeline] Fetch the weekly input bundle from the dead-drop mailbox.

Power Automate reads the SharePoint input folder and mails every pipeline input
as an attachment to a dedicated mailbox. This script pulls the newest matching
mail via IMAP and writes each attachment to its place under PeerGrading/Input/.

Expected attachments (matched on filename, case-insensitive):
  forms_responses.xlsx   the Forms export
  StudentListDB.xlsx     the roster
  week_windows.csv       week definitions - shift every year, so they come
                         from SharePoint rather than living in the repo
  semester.csv           semester metadata + expiry dates (see semester_guard.py)

Required env vars (GitHub secrets):
  MAIL_IMAP_USER       dead-drop mailbox address (e.g. xxx@gmail.com)
  MAIL_IMAP_PASSWORD   app password for the mailbox
  MAIL_ALLOWED_FROM    only mails from this sender are trusted

Optional env vars:
  MAIL_IMAP_HOST       default: imap.gmail.com
  MAIL_SUBJECT_FILTER  substring the subject must contain
                       (default: "PeerGrading Data Sync")

Behaviour:
  - Only UNSEEN mails from MAIL_ALLOWED_FROM are considered, and the sender is
    re-checked against the From header (IMAP FROM search matches substrings).
  - The newest one (by Date header) wins.
  - Every required attachment must be present and must pass validation; nothing
    is written to disk until all of them have. A partial bundle is an error, so
    the pipeline never recomputes against a half-updated input set.
  - Matched mails are marked \\Seen only AFTER a successful write, so a failed
    run retries them next time.
  - Exit 0 both on success and on "no new mail". Exit 1 on real errors.
"""

from __future__ import annotations

import email
import imaplib
import io
import os
import sys
import zipfile
from datetime import datetime, timezone
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Attachment filename (lowercased) -> destination path. Structure lives here;
# the semester-dependent content of these files lives on SharePoint.
DESTINATIONS: dict[str, str] = {
    "forms_responses.xlsx": "PeerGrading/Input/form_exports/forms_responses.xlsx",
    "studentlistdb.xlsx": "PeerGrading/Input/students_db/StudentListDB.xlsx",
    "week_windows.csv": "PeerGrading/Input/week_setup/week_windows.csv",
    "semester.csv": "PeerGrading/Input/semester.csv",
}

REQUIRED = set(DESTINATIONS)


def _require(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        print(f"ERROR: required env var {name} is not set.")
        sys.exit(1)
    return v


def _collect_attachments(msg: Message) -> dict[str, bytes]:
    """Return {lowercased filename: payload} for attachments we care about."""
    found: dict[str, bytes] = {}
    for part in msg.walk():
        fname = (part.get_filename() or "").strip()
        if not fname:
            continue
        key = Path(fname).name.lower()
        if key not in DESTINATIONS:
            continue
        payload = part.get_payload(decode=True)
        if payload:
            found[key] = payload
    return found


def _validate(name: str, payload: bytes) -> str | None:
    """Return an error message, or None when the payload looks sane."""
    if not payload:
        return "attachment is empty"

    if name.endswith(".xlsx"):
        # An xlsx is a zip container starting with "PK".
        if not payload.startswith(b"PK"):
            return f"not a valid xlsx (first bytes: {payload[:20]!r})"
        try:
            zipfile.ZipFile(io.BytesIO(payload)).testzip()
        except zipfile.BadZipFile:
            return "not a valid xlsx (corrupt zip)"
        return None

    if name.endswith(".csv"):
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            return "csv is not valid UTF-8"
        if not text.strip():
            return "csv is empty"
        return None

    return None


def _sort_key(item) -> datetime:
    d = item[0]
    if d is None:
        return EPOCH
    if d.tzinfo is None:
        return d.replace(tzinfo=timezone.utc)
    return d


def main() -> None:
    host = os.getenv("MAIL_IMAP_HOST", "imap.gmail.com").strip()
    user = _require("MAIL_IMAP_USER")
    password = _require("MAIL_IMAP_PASSWORD")
    allowed_from = _require("MAIL_ALLOWED_FROM").lower()
    subject_filter = os.getenv("MAIL_SUBJECT_FILTER", "PeerGrading Data Sync").strip()

    imap = imaplib.IMAP4_SSL(host)
    imap.login(user, password)
    imap.select("INBOX")

    status, data = imap.search(None, "UNSEEN", "FROM", f'"{allowed_from}"')
    if status != "OK":
        print(f"ERROR: IMAP search failed: {status}")
        sys.exit(1)

    ids = data[0].split()
    if not ids:
        print("No new mail from trusted sender - nothing to do.")
        imap.logout()
        return

    # Fetch candidates without marking them seen (BODY.PEEK), newest wins.
    candidates = []
    for mid in ids:
        status, msg_data = imap.fetch(mid, "(BODY.PEEK[])")
        if status != "OK" or not msg_data or msg_data[0] is None:
            continue
        msg = email.message_from_bytes(msg_data[0][1])

        sender = parseaddr(msg.get("From", ""))[1].lower()
        if sender != allowed_from:
            print(f"Skipping mail {mid.decode()}: sender '{sender}' is not trusted.")
            continue

        subject = str(msg.get("Subject", ""))
        if subject_filter and subject_filter.lower() not in subject.lower():
            print(f"Skipping mail {mid.decode()}: subject does not match filter.")
            continue

        try:
            date = parsedate_to_datetime(msg["Date"])
        except Exception:
            date = None
        candidates.append((date, mid, msg))

    if not candidates:
        print("No mail matched sender/subject filters - nothing to do.")
        imap.logout()
        return

    candidates.sort(key=_sort_key)
    date, mid, msg = candidates[-1]

    attachments = _collect_attachments(msg)

    missing = sorted(REQUIRED - set(attachments))
    if missing:
        print(f"ERROR: newest matching mail (dated {date}) is missing required attachments: {missing}")
        print(f"       attachments found: {sorted(attachments) or 'none'}")
        sys.exit(1)

    # Validate everything before touching the working tree.
    errors = []
    for name in sorted(attachments):
        err = _validate(name, attachments[name])
        if err:
            errors.append(f"  {name}: {err}")
    if errors:
        print("ERROR: attachment validation failed:")
        print("\n".join(errors))
        sys.exit(1)

    for name in sorted(attachments):
        dest = Path(DESTINATIONS[name])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(attachments[name])
        print(f"OK: wrote {len(attachments[name])} bytes to {dest}")

    print(f"Bundle from mail dated {date} written successfully.")

    # Mark all matched mails as seen only after a successful write.
    for _, cmid, _ in candidates:
        imap.store(cmid, "+FLAGS", "\\Seen")
    imap.logout()


if __name__ == "__main__":
    main()
