# PeerGrading

Weekly peer-grading pipeline for the course. A Microsoft Forms export is ingested,
scored against the rubric, and turned into per-week summaries, attendance lists and
email-ready drafts. Everything runs in GitHub Actions; the results are committed back
into this repo.

## How it runs

`.github/workflows/mail_sync.yml` (scheduled Sunday 06:15 Europe/Zurich, plus
`workflow_dispatch`):

1. `scripts/fetch_mail.py` pulls the input bundle out of the dead-drop mailbox over IMAP
   and validates every attachment.
2. `scripts/semester_guard.py` checks the expiry dates. Past the deadline the run stops
   here.
3. `scripts/compute_weekly.py --week ALL` recomputes the outputs.
4. `scripts/push_outputs.py --mode weekly` mails the output bundle back out, for Power
   Automate to write into SharePoint.
5. A PR on `bot/mail-sync-outputs` carries the changes, with squash auto-merge enabled.

Secrets: `MAIL_IMAP_USER`, `MAIL_IMAP_PASSWORD` (used for both IMAP and SMTP),
`MAIL_ALLOWED_FROM`, `MAIL_OUTBOX_TO`.

`.github/workflows/compute_weekly.yml` is the previous pipeline: it pulls the Forms export
from a SharePoint share link that has to be re-shared monthly. It still runs, and is meant
to be deleted — along with the `FORMS_XLSX_URL` secret — once the mail sync is verified.

## Where the data lives

Every input is maintained in one SharePoint folder, not in this repo. Weeks and semester
boundaries shift from year to year, so keeping them in the repo would mean a code change
each semester. Power Automate mails the folder contents to the dead-drop mailbox once a
week, and `scripts/fetch_mail.py` unpacks them:

| Attachment | Lands at | What it is |
| --- | --- | --- |
| `forms_responses.xlsx` | `PeerGrading/Input/form_exports/` | The Forms export |
| `StudentListDB.xlsx` | `PeerGrading/Input/students_db/` | Roster, used for attendance |
| `week_windows.csv` | `PeerGrading/Input/week_setup/` | Week definitions |
| `semester.csv` | `PeerGrading/Input/` | Semester metadata and expiry dates |

All four must be attached to the same mail, with `PeerGrading Data Sync` in the subject.
A partial bundle fails the run rather than recomputing against half-updated inputs.

`week_windows.csv` drives week assignment. Columns:
`week_id, start_local, end_local, deadline_local, tz`. A response belongs to a week when
`start_local <= timestamp < end_local`, and is kept only if it arrived before
`deadline_local`. Adding or shifting a week means editing this file on SharePoint —
nothing else.

## Expiry

`semester.csv` is a two-column key/value file and is what stops the pipeline from running
past the end of the semester:

```csv
key,value
semester_id,2026-FS
semester_end,2026-06-14
tz,Europe/Zurich
ta_email,ta@uzh.ch
archive_url,
```

A date with no time means the whole of that day is still valid. Individual inputs can be
retired earlier than the semester itself; anything not listed inherits `semester_end`:

```csv
expires:forms_responses.xlsx,2026-06-07
```

Once a deadline has passed, `scripts/semester_guard.py` stops the run before any
computation happens. The stop is silent — there is no warning window and no build noise in
the weeks leading up to it. The TA is mailed once, from the dead-drop mailbox, telling them
the last week has been calculated and that their files should be downloaded before they are
moved to the Archive; fill in `archive_url` and that link goes into the mail. A marker at
`PeerGrading/Output/semester_closed.txt` is committed so later scheduled runs stay quiet.

Starting a new semester is a matter of updating `semester.csv` and `week_windows.csv` on
SharePoint. If `semester.csv` is missing, the guard refuses to run at all rather than
compute against data it cannot date-check.

Expected columns in the Forms export: `ResponseId`, `ReceivedAtUTC` (falls back to
`SubmittedAt`), `RaterEmail`, `RaterName`, `PresenterChoice`, `Q2_1`–`Q2_3`, `Q3_1`–`Q3_3`,
`Q4` (or `Q_4`), `Comment`. Lookup is case-insensitive; a missing question column is a
hard error.

## Scoring

Likert cells are parsed to 1–5 (`"Good 4"` → 4, trailing digit, or a bare number).
A response is dropped if any of the seven questions is unparseable.

```
score = 0.1 * (Q2_1 + Q2_2 + Q2_3 + Q3_1 + Q3_2 + Q3_3) + 0.4 * Q4
```

Within a week, repeated grades from the same rater for the same presenter collapse to the
latest submission. A presenter's weekly score is the mean across raters.

## Outputs

Written under `PeerGrading/Output/`:

- `weekly_status.csv` — one row per week defined in `week_windows.csv`:
  `week_id, start_local, end_local, deadline_local, n_responses, n_presenters, status`.
  `status` is `pending` (window has not opened), `no_data` (window passed, nothing was
  submitted) or `computed`. Start here to see week by week what happened.
- `weekly_summary_<week>.csv` — per presenter: n_raters, mean/std/min/max, per-question means
- `weekly_summary_ALL.csv` — the same across every week in the run
- `peer_log_<week>.csv` — the deduped rater-level rows behind those numbers
- `attendance_<week>.csv` — full roster joined against submissions, with a `submitted` flag
- `mails/<week>/<Name>.txt` — email draft per presenter, for reading and archiving
- `drafts/<week>/<Name>.eml` — the same mail as an Outlook-importable draft
- `unresolved_presenters.csv` — presenters whose name did not match a roster entry, so no
  `.eml` could be addressed. Should normally be empty.
- `master/grade_edges.csv` — cumulative across runs, deduped on `ResponseId`
- `master/grade_matrix_{count,mean}.csv` — rater × presenter matrices over all weeks

Every week whose window has opened gets a `weekly_summary`, `peer_log` and `attendance`
file, even when nobody submitted anything: the summary and log are written with headers
only, and the attendance list shows the whole roster at zero. An absent file means the
pipeline never ran for that week, not that the week was empty.

### Draft mails

`drafts/<week>/*.eml` carry the presenter's address in `To:`, resolved by matching the
Forms `PresenterChoice` value against `First name + Last name` in the roster. There is
deliberately no `From:` header — Outlook fills in whichever account you drop the file into.

To use them: download the week's folder and drag the `.eml` files into your Outlook
**Drafts**, then review and send by hand. This works in Outlook desktop; Outlook on the
web does not support importing `.eml` files.

## Sending outputs back to SharePoint

`scripts/push_outputs.py` zips the outputs and mails them to `MAIL_OUTBOX_TO`. A second
Power Automate flow watches that mailbox and writes the attachment into SharePoint — the
mirror of the inbound flow, so the round trip needs no share links and no app
registration.

| Mode | Subject | Contents | Where Power Automate should file it |
| --- | --- | --- | --- |
| `--mode weekly` | `PeerGrading Outputs <semester_id>` | the current `PeerGrading/Output` tree | the semester's output folder |
| `--mode archive` | `PeerGrading Archive <semester_id>` | input snapshot + all outputs + `MANIFEST.txt` | `Archive/<semester_id>/` |

The archive is built once, by `semester_guard.py`, at the moment the semester expires —
before the closure mail goes out, so the Archive exists by the time the TA is told about
it. If the archive fails to send, the closure is not recorded and the next run retries.

`--dry-run` writes the zip to disk instead of mailing it. Bundles larger than 20 MB
(`--max-mb`) are refused rather than silently rejected by the mail server.

## Running it by hand

```bash
pip install -r requirements.txt
python scripts/compute_weekly.py --week ALL
```

Useful flags: `--week W04` for a single week, `--responses` / `--students` /
`--week-windows` to point at other files, `--out-dir` to write elsewhere, `--tz` to change
the local timezone (default `Europe/Zurich`).

## Note on data

The roster and the Forms export are committed to this repo, so real student names, email
addresses and grades live in the git history. `.gitignore` lists `PeerGrading/Input/**`,
but that rule has no effect on files that are already tracked.
