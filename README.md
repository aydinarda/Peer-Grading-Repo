# PeerGrading

Weekly peer-grading pipeline for the course. A Microsoft Forms export is scored against
the rubric and turned into per-week summaries, attendance lists and ready-to-send feedback
mails.

This repo holds only code. All course data — inputs, outputs, archives — lives in a
SharePoint document library synced to the machine that runs the tool. See
[HOWTO.txt](HOWTO.txt) for the operator-facing instructions; that file is meant to be
copied into the SharePoint folder itself.

## Design

The tool runs locally, on macOS or Windows, against the synced SharePoint folder. It uses
**no credentials of any kind** — no tokens, no app passwords, no API keys, and nothing that
expires. The OneDrive client puts the library on disk; the tool reads and writes it as an
ordinary folder, and OneDrive syncs the results back.

That choice is about succession. This system gets handed from one person to the next, and
anything tied to an individual account — a Power Automate flow, an Azure app registration,
a mailbox app password — dies when that person leaves the university, or needs renewing by
someone who has no idea it exists. Access is managed where it belongs: SharePoint
permissions on the folder. Handover is one act, granting access to that folder.

It also fails safe. If nobody runs it, nothing happens and nothing is lost; the next run
recomputes everything from the folder.

`scripts/paths.py` is the seam that keeps this reversible. Every other module asks it for
`inputs()` / `outputs()` / `archive()` and never learns how the folder got there, so moving
to the Microsoft Graph API later means rewriting that one file.

## Running it

```bash
./run.sh              # macOS — every week
./run.sh --week W04   # one week
.\run.ps1             # Windows
```

`run.sh` / `run.ps1` create the virtualenv on first use, run the expiry guard, then
compute. For automatic weekly runs see [scripts/scheduling/](scripts/scheduling/).

First time on a machine: `python3 scripts/setup_local.py` finds the synced folder and
records it in `.peergrading.json` (gitignored). Override with `--root <path>` or
`PEERGRADING_ROOT`; precedence is `--root` > env > config file.

## Data folder

```
PeerGrading/
  Input/
    form_exports/forms_responses.xlsx
    students_db/StudentListDB.xlsx
    week_setup/week_windows.csv
    semester.csv                       semester dates, TA address
    rubric.csv                         per-question weights
    mail_template.txt                  the feedback mail wording
  Output/
  Archive/<semester_id>/
```

`week_windows.csv` (`week_id, start_local, end_local, deadline_local, tz`) drives week
assignment: a response belongs to a week when `start_local <= timestamp < end_local`, and
counts only if it arrived before `deadline_local`. Both this file and `semester.csv` shift
every year, which is exactly why they live with the data rather than in the code.

Expected columns in the Forms export: `ResponseId`, `ReceivedAtUTC` (falls back to
`SubmittedAt`), `RaterEmail`, `RaterName`, `PresenterChoice`, `Q2_1`–`Q2_3`, `Q3_1`–`Q3_3`,
`Q4` (or `Q_4`), `Comment`. Lookup is case-insensitive; a missing question column is a hard
error.

## Course policy vs. code

Anything the course owns lives in the data folder, so a new teaching assistant never has
to open a Python file:

| What | Where |
| --- | --- |
| Semester dates, timezone, TA address, archive link | `Input/semester.csv` |
| Week boundaries and deadlines | `Input/week_setup/week_windows.csv` |
| Per-question weights | `Input/rubric.csv` |
| Feedback mail subject, wording, signature | `Input/mail_template.txt` |

`rubric.csv` and `mail_template.txt` are written out with the current defaults the first
time they are missing, so the folder advertises what can be changed instead of hiding it.
`scripts/course_config.py` reads them and reports mistakes by name — an unknown
`{placeholder}`, a weight that is not a number, a missing question.

What deliberately stays in code: which questions exist, the Likert label mapping, and the
expected Forms column names. Those describe the shape of the export rather than course
policy, and moving them into a spreadsheet would relocate the confusing part rather than
remove it.

## Scoring

Likert cells are parsed to 1–5 (`"Good 4"` → 4, trailing digit, or a bare number). A
response is dropped if any of the seven questions is unparseable.

```
score = Σ (weight[q] × answer[q])      weights from Input/rubric.csv
```

The defaults are `0.1` for each of the six statements and `0.4` for the general grade,
summing to 1 and putting the score on a 1–5 scale. Weights that sum to something else are
allowed — the run says so, and the mails quote the recalculated maximum.

Within a week, repeated grades from the same rater for the same presenter collapse to the
latest submission. A presenter's weekly score is the mean across raters.

## Outputs

- `weekly_status.csv` — one row per week in `week_windows.csv`, with `status` of `pending`,
  `no_data` or `computed`. Start here.
- `weekly_summary_<week>.csv` — per presenter: n_raters, mean/std/min/max, per-question means
- `weekly_summary_ALL.csv` — the same across every week in the run
- `peer_log_<week>.csv` — the deduped rater-level rows behind those numbers
- `attendance_<week>.csv` — full roster joined against submissions, with a `submitted` flag
- `mails/<week>/<Name>.txt` — feedback mail as plain text
- `drafts/<week>/<Name>.eml` — the same mail, Outlook-importable
- `unresolved_presenters.csv` — presenters whose name did not match the roster
- `master/grade_edges.csv` — cumulative across runs, deduped on `ResponseId`
- `master/grade_matrix_{count,mean}.csv` — rater × presenter matrices over all weeks

Every week whose window has opened gets a summary, log and attendance file even when
nobody submitted: summary and log are headers only, attendance shows the whole roster at
zero. An absent file means the tool never ran for that week, not that the week was empty.

### Draft mails

`drafts/<week>/*.eml` carry the presenter's address in `To:`, resolved by matching the
Forms `PresenterChoice` value against `First name + Last name` in the roster. There is
deliberately no `From:` header — Outlook fills in whichever account you drop the file into.

Drag them into Outlook **Drafts**, review, and send by hand. The tool never sends mail.
This works in Outlook desktop; Outlook on the web cannot import `.eml`.

## End of semester

`scripts/semester_guard.py` reads the expiry dates from `semester.csv` and runs before
every computation. Past `semester_end` it stops the run — silently, with no warning window
— and on the first such run it copies the input snapshot, all outputs and a `MANIFEST.txt`
into `Archive/<semester_id>/`, then writes `SEMESTER_CLOSED.txt` and a
`semester_closed.eml` draft addressed to `ta_email` there.

A marker at `Output/semester_closed.txt` keeps later runs quiet. If archiving fails the
marker is not written, so the next run retries the whole closure rather than leaving a
notice pointing at an archive that was never produced.

Per-file expiry overrides are supported; anything not listed inherits `semester_end`:

```csv
expires:forms_responses.xlsx,2026-06-07
```

Guard exit codes, which the run wrappers act on: `0` active, `2` expired (stop quietly),
`1` could not decide.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite builds synthetic data folders and runs the real scripts against them
as subprocesses. Fixtures live in [tests/factories.py](tests/factories.py) and
reproduce the awkward parts of the actual Forms export — an unresolved
`utcNow()` timestamp, a `ResponseId` reused across responses, presenter names
separated by tabs or newlines, non-ASCII names, `Q_4` instead of `Q4`. No real
course data is used: names and addresses are invented, so student data stays out
of this repo.

Week windows are generated relative to today, so `pending` / `no_data` /
`computed` stay meaningful whenever the suite runs.

Everything the tests generate is left in `artifacts/<test_name>/` (gitignored)
so you can open the CSVs and drag a generated `.eml` into Outlook to see what a
real one looks like.

[tests/test_no_network.py](tests/test_no_network.py) blocks socket connections
and runs the whole pipeline, then greps the scripts for mail and credential
machinery — the tool is not supposed to be able to reach anything, and that is
checked rather than assumed.

Two tests are `xfail(strict=True)`, documenting the `grade_edges.csv`
deduplication bug described below. They will start failing — and should be
un-marked — once it is fixed.

### Known bug: `master/grade_edges.csv` grows on every run

The cumulative edge file is re-read with `ResponseId` typed as `int64`, while
freshly computed rows carry it as `str`. `drop_duplicates` therefore never
matches an old row against a new one, so each run appends the entire history
again. Attendance counts are derived from this file, so they inflate with it.

## Layout

| Path | Role |
| --- | --- |
| `scripts/paths.py` | resolves the data root; the storage seam |
| `scripts/setup_local.py` | one-time per-machine setup |
| `scripts/compute_weekly.py` | the computation and all output generation |
| `scripts/semester_guard.py` | expiry check, archiving, closure notice |
| `scripts/semester_config.py` | shared `semester.csv` reader |
| `scripts/course_config.py` | rubric weights and mail template |
| `scripts/scheduling/` | launchd and Task Scheduler setup |
