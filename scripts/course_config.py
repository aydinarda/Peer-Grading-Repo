#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Course policy that belongs to whoever runs the course, not to the code.

Two things live here: how the rubric answers are weighted, and what the feedback
mail says. Both change when the course changes hands or the rubric is revised,
and neither should require opening a Python file.

They are read from the data folder:

    Input/rubric.csv          question,weight
    Input/mail_template.txt   the mail, with {placeholders}

If either file is missing it is written out with the defaults below, so the
folder always shows what can be edited rather than hiding it in the code.

What deliberately stays in code: which questions exist, the Likert label
mapping, and the expected Forms column names. Those describe the shape of the
export rather than course policy, and turning them into config would move the
part nobody understands from Python into a spreadsheet without making it any
easier to get right.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import paths

# The rubric question set. Changing this is a code change - see the module note.
RUBRIC_QUESTIONS = ["Q2_1", "Q2_2", "Q2_3", "Q3_1", "Q3_2", "Q3_3", "Q4"]

DEFAULT_WEIGHTS = {
    "Q2_1": 0.1, "Q2_2": 0.1, "Q2_3": 0.1,
    "Q3_1": 0.1, "Q3_2": 0.1, "Q3_3": 0.1,
    "Q4": 0.4,
}

SCALE_MAX = 5

DEFAULT_TEMPLATE = """Subject: Peer feedback summary ({week_id})

Hi {presenter},

Here is your peer-assessment summary for {week_id}:
- Number of reviewers: {n_raters}
- Final score (weighted): {mean_score} / {max_score}

Per-question averages (1–5):
{question_averages}

{comments}

Best regards,
Course team
"""

TEMPLATE_HELP = """\
Placeholders you can use:
  {week_id}            the week, e.g. W04
  {presenter}          the presenter's name
  {n_raters}           how many people graded them
  {mean_score}         their weighted score, two decimals
  {max_score}          the highest score possible, from rubric.csv
  {question_averages}  one line per question, filled in for you
  {comments}           the written comments, filled in for you

The first line becomes the mail subject. Keep the blank line after it.
"""


def fail(msg: str) -> None:
    print(f"ERROR: {msg}")
    sys.exit(1)


def load_weights(path: Path | None = None) -> dict[str, float]:
    """Read Input/rubric.csv, seeding it with the defaults if absent."""
    path = path or paths.rubric_file()
    if not path.exists():
        _seed_rubric(path)
        return dict(DEFAULT_WEIGHTS)

    weights: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip() or row[0].lstrip().startswith("#"):
                continue
            key = row[0].strip()
            if key.lower() == "question":
                continue  # header
            if len(row) < 2 or not row[1].strip():
                fail(f"{path}: question '{key}' has no weight.")
            try:
                weights[key] = float(row[1].strip())
            except ValueError:
                fail(f"{path}: weight for '{key}' is not a number: {row[1]!r}")

    missing = [q for q in RUBRIC_QUESTIONS if q not in weights]
    if missing:
        fail(f"{path} is missing weights for: {', '.join(missing)}")

    unknown = [q for q in weights if q not in RUBRIC_QUESTIONS]
    if unknown:
        fail(f"{path} has weights for unknown questions: {', '.join(unknown)}")

    total = sum(weights.values())
    if abs(total - 1.0) > 1e-9:
        # Not an error - a course may want a different scale - but the change in
        # the top score is worth saying out loud.
        print(
            f"NOTE: weights in {path} add up to {total:g}, not 1. "
            f"The highest possible score is now {total * SCALE_MAX:.2f}."
        )
    return weights


def max_score(weights: dict[str, float]) -> float:
    return sum(weights.values()) * SCALE_MAX


def load_mail_template(path: Path | None = None) -> str:
    """Read Input/mail_template.txt, seeding it with the default if absent."""
    path = path or paths.mail_template_file()
    if not path.exists():
        _seed_template(path)
        return DEFAULT_TEMPLATE

    text = path.read_text(encoding="utf-8-sig")
    # Lines starting with # are notes to the editor, not part of the mail.
    text = "\n".join(l for l in text.splitlines() if not l.startswith("#")).strip("\n")
    if not text.strip():
        fail(f"{path} is empty.")
    return text + "\n"


def render_mail(template: str, **fields) -> tuple[str, str]:
    """Fill the template in and split it into (subject, body)."""
    try:
        rendered = template.format(**fields)
    except KeyError as exc:
        fail(
            f"{paths.mail_template_file()} uses an unknown placeholder {exc}. "
            f"Known placeholders: {', '.join(sorted(fields))}."
        )
    except (IndexError, ValueError) as exc:
        fail(
            f"{paths.mail_template_file()} could not be filled in: {exc}. "
            "A stray { or } is the usual cause - write {{ or }} for a literal brace."
        )

    lines = rendered.splitlines()
    if not lines or not lines[0].lower().startswith("subject:"):
        fail(
            f"{paths.mail_template_file()} must start with a 'Subject: ...' line, "
            "followed by a blank line."
        )

    subject = lines[0].split(":", 1)[1].strip()
    body = "\n".join(lines[1:]).strip("\n")
    return subject, body


def _seed_rubric(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# How much each answer counts towards the final score.",
        "# The weights normally add up to 1, which puts the score on a 1-5 scale.",
        "question,weight",
    ]
    lines.extend(f"{q},{DEFAULT_WEIGHTS[q]}" for q in RUBRIC_QUESTIONS)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Created {path} with the default weights - edit it to change the rubric.")


def _seed_template(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    commented_help = "".join(f"# {line}\n" for line in TEMPLATE_HELP.splitlines())
    path.write_text(DEFAULT_TEMPLATE + "\n" + commented_help, encoding="utf-8")
    print(f"Created {path} with the default wording - edit it to change the mail.")
