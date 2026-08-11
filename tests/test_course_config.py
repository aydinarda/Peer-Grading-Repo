"""Course policy that lives in the data folder: the rubric and the mail wording.

These are the two things a new teaching assistant is most likely to want to
change, so they have to be editable without opening any Python.
"""

from __future__ import annotations

import email
from email import policy

import pandas as pd
import pytest

import course_config
import factories as f
from factories import Response, make_root, presenter_choice, uniform

ANA, BJORN, CARLOS = f.ROSTER[:3]


def base_responses():
    return [
        Response(1, "W01", ANA, presenter_choice(CARLOS), uniform(5), "good work"),
        Response(2, "W01", BJORN, presenter_choice(CARLOS), uniform(3)),
    ]


def load_eml(root, name="Carlos_Diaz_Ruiz"):
    path = root.drafts("W01") / f"{name}.eml"
    return email.message_from_bytes(path.read_bytes(), policy=policy.default)


class TestSeeding:
    """A fresh folder should show what can be edited, not hide it in the code."""

    def test_both_files_are_created_on_first_run(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        assert not (root.inputs / "rubric.csv").exists()
        assert not (root.inputs / "mail_template.txt").exists()

        compute(root.path)

        assert (root.inputs / "rubric.csv").exists()
        assert (root.inputs / "mail_template.txt").exists()

    def test_seeded_rubric_holds_the_defaults(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        compute(root.path)

        weights = course_config.load_weights(root.inputs / "rubric.csv")
        assert weights == course_config.DEFAULT_WEIGHTS

    def test_seeded_template_explains_itself(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        compute(root.path)

        text = (root.inputs / "mail_template.txt").read_text(encoding="utf-8")
        assert "{presenter}" in text
        assert "# Placeholders you can use:" in text

    def test_seeding_does_not_change_the_result(self, artifact_dir, compute):
        """Defaults must reproduce the wording the course already sends out."""
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        compute(root.path)
        body = load_eml(root).get_content()

        assert body.startswith("Hi Carlos Diaz Ruiz,")
        assert "- Number of reviewers: 2" in body
        assert "- Final score (weighted): 4.00 / 5.00" in body
        assert "- Q4  : 4.00" in body
        assert body.rstrip().endswith("Course team")


class TestRubricWeights:
    def test_changed_weights_change_the_score(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        compute(root.path)
        before = pd.read_csv(root.week("W01") / "summary.csv").iloc[0]["mean_score"]

        # Everything on the general grade, nothing on the six statements.
        (root.inputs / "rubric.csv").write_text(
            "question,weight\n"
            "Q2_1,0\nQ2_2,0\nQ2_3,0\nQ3_1,0\nQ3_2,0\nQ3_3,0\nQ4,1.0\n",
            encoding="utf-8",
        )
        compute(root.path)
        after = pd.read_csv(root.week("W01") / "summary.csv").iloc[0]["mean_score"]

        assert before == pytest.approx(4.0)
        assert after == pytest.approx(4.0)   # scores were uniform, so the mean holds
        # but a lopsided response set must now be driven by Q4 alone:
        root.rewrite_forms([
            Response(1, "W01", ANA, presenter_choice(CARLOS),
                     {"Q2_1": 1, "Q2_2": 1, "Q2_3": 1, "Q3_1": 1, "Q3_2": 1, "Q3_3": 1, "Q4": 5}),
        ])
        compute(root.path)
        q4_only = pd.read_csv(root.week("W01") / "summary.csv").iloc[0]["mean_score"]
        assert q4_only == pytest.approx(5.0)

    def test_non_unit_weights_are_reported_and_rescale_the_maximum(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        (root.inputs / "rubric.csv").write_text(
            "question,weight\n"
            "Q2_1,1\nQ2_2,1\nQ2_3,1\nQ3_1,1\nQ3_2,1\nQ3_3,1\nQ4,1\n",
            encoding="utf-8",
        )
        proc = compute(root.path)

        assert "add up to 7" in proc.stdout
        assert "highest possible score is now 35.00" in proc.stdout
        # The mail must quote the real maximum, not a stale 5.00.
        assert "/ 35.00" in load_eml(root).get_content()

    def test_missing_question_is_refused(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        (root.inputs / "rubric.csv").write_text(
            "question,weight\nQ2_1,0.5\nQ4,0.5\n", encoding="utf-8"
        )
        proc = compute(root.path, expect=1)
        assert "missing weights for" in proc.stdout

    def test_unknown_question_is_refused(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        text = "question,weight\n" + "".join(
            f"{q},0.1\n" for q in course_config.RUBRIC_QUESTIONS
        ) + "Q9_9,0.3\n"
        (root.inputs / "rubric.csv").write_text(text, encoding="utf-8")
        proc = compute(root.path, expect=1)
        assert "unknown questions" in proc.stdout

    def test_non_numeric_weight_is_refused(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        text = "question,weight\n" + "".join(
            f"{q},0.1\n" for q in course_config.RUBRIC_QUESTIONS[:-1]
        ) + "Q4,heavy\n"
        (root.inputs / "rubric.csv").write_text(text, encoding="utf-8")
        proc = compute(root.path, expect=1)
        assert "not a number" in proc.stdout

    def test_comments_and_blank_lines_are_tolerated(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        text = "# our rubric\n\nquestion,weight\n" + "".join(
            f"{q},{course_config.DEFAULT_WEIGHTS[q]}\n" for q in course_config.RUBRIC_QUESTIONS
        )
        (root.inputs / "rubric.csv").write_text(text, encoding="utf-8")
        compute(root.path)
        assert pd.read_csv(root.week("W01") / "summary.csv").iloc[0][
            "mean_score"] == pytest.approx(4.0)


class TestMailTemplate:
    def test_wording_can_be_replaced_entirely(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        (root.inputs / "mail_template.txt").write_text(
            "Subject: {week_id} feedback\n"
            "\n"
            "Merhaba {presenter},\n"
            "\n"
            "Puanin: {mean_score}/{max_score} ({n_raters} degerlendirici)\n"
            "\n"
            "{question_averages}\n"
            "\n"
            "{comments}\n"
            "\n"
            "Iyi calismalar,\n"
            "Ders ekibi\n",
            encoding="utf-8",
        )
        compute(root.path)

        msg = load_eml(root)
        assert msg["Subject"] == "W01 feedback"
        body = msg.get_content()
        assert body.startswith("Merhaba Carlos Diaz Ruiz,")
        assert "Puanin: 4.00/5.00 (2 degerlendirici)" in body
        assert "Ders ekibi" in body
        assert "Best regards" not in body

    def test_generated_blocks_are_filled_in(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        (root.inputs / "mail_template.txt").write_text(
            "Subject: x\n\n{question_averages}\n---\n{comments}\n", encoding="utf-8"
        )
        compute(root.path)
        body = load_eml(root).get_content()

        assert "- Q2_1: 4.00" in body
        assert "- Q4  : 4.00" in body
        assert "Comments:" in body
        assert "1. good work" in body

    def test_txt_and_eml_stay_in_step(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        (root.inputs / "mail_template.txt").write_text(
            "Subject: Custom {week_id}\n\nHello {presenter}\n", encoding="utf-8"
        )
        compute(root.path)

        txt = (root.mails("W01") / "Carlos_Diaz_Ruiz.txt").read_text(encoding="utf-8")
        assert txt.startswith("Subject: Custom W01")
        assert txt.split("\n\n", 1)[1].strip() == load_eml(root).get_content().strip()

    def test_unknown_placeholder_is_named(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        (root.inputs / "mail_template.txt").write_text(
            "Subject: x\n\nHi {presentor},\n", encoding="utf-8"
        )
        proc = compute(root.path, expect=1)
        assert "unknown placeholder" in proc.stdout
        assert "presentor" in proc.stdout
        # The message must also say what is available.
        assert "presenter" in proc.stdout

    def test_stray_brace_is_explained(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        (root.inputs / "mail_template.txt").write_text(
            "Subject: x\n\nScore: 100{ percent\n", encoding="utf-8"
        )
        proc = compute(root.path, expect=1)
        assert "stray { or }" in proc.stdout

    def test_missing_subject_line_is_refused(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        (root.inputs / "mail_template.txt").write_text(
            "Hi {presenter},\n\nno subject here\n", encoding="utf-8"
        )
        proc = compute(root.path, expect=1)
        assert "must start with a 'Subject: ...' line" in proc.stdout

    def test_empty_template_is_refused(self, artifact_dir, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses())
        (root.inputs / "mail_template.txt").write_text("# only a note\n", encoding="utf-8")
        proc = compute(root.path, expect=1)
        assert "is empty" in proc.stdout


class TestArchivedConfig:
    def test_rubric_and_template_end_up_in_the_archive(self, artifact_dir, compute, guard):
        """The archive has to explain how its numbers and wording were produced."""
        from datetime import datetime, timedelta

        root = make_root(artifact_dir / "PeerGrading", base_responses(),
                         semester_end=(datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d"))
        compute(root.path)

        df = pd.read_csv(root.inputs / "semester.csv")
        df.loc[df["key"] == "semester_end", "value"] = (
            datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        df.to_csv(root.inputs / "semester.csv", index=False)

        guard(root.path, expect=2)

        archived = {p.name for p in (root.archive / "2026-TEST" / "Input").rglob("*") if p.is_file()}
        assert {"rubric.csv", "mail_template.txt", "semester.csv"} <= archived
