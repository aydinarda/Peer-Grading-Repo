"""The generated feedback mails - the artefacts that get dragged into Outlook."""

from __future__ import annotations

import email
from email import policy

import pandas as pd
import pytest

import factories as f
from factories import Response, make_root, presenter_choice, uniform, scores

ANA, BJORN, CARLOS, DANA, EMIL = f.ROSTER[:5]


def load_eml(path):
    return email.message_from_bytes(path.read_bytes(), policy=policy.default)


@pytest.fixture
def drafted(artifact_dir, compute):
    """One week, three presenters, covering each separator style."""
    responses = [
        Response(1, "W01", ANA, presenter_choice(BJORN, sep="\t"), uniform(5), "clear and calm"),
        Response(2, "W01", DANA, presenter_choice(BJORN, sep="\t"), scores(4, 4, 3, 5, 4, 4, 4)),
        Response(3, "W01", ANA, presenter_choice(CARLOS, sep=" "), uniform(3)),
        Response(4, "W01", BJORN, presenter_choice(EMIL, sep="\n"), uniform(2), "needs structure"),
    ]
    root = make_root(artifact_dir / "PeerGrading", responses)
    compute(root.path)
    return root


class TestAddressing:
    def test_one_draft_per_presenter(self, drafted):
        drafts = sorted((drafted.outputs / "drafts" / "W01").glob("*.eml"))
        assert len(drafts) == 3

    def test_recipients_come_from_the_roster(self, drafted):
        recipients = {
            load_eml(p)["To"]
            for p in (drafted.outputs / "drafts" / "W01").glob("*.eml")
        }
        assert recipients == {BJORN.email, CARLOS.email, EMIL.email}

    def test_no_from_header(self, drafted):
        for p in (drafted.outputs / "drafts" / "W01").glob("*.eml"):
            assert load_eml(p)["From"] is None

    def test_subject_names_the_week(self, drafted):
        for p in (drafted.outputs / "drafts" / "W01").glob("*.eml"):
            assert load_eml(p)["Subject"] == "Peer feedback summary (W01)"


class TestContent:
    def test_non_ascii_survives(self, drafted):
        path = drafted.outputs / "drafts" / "W01" / "Bjrn_Sjberg.eml"
        body = load_eml(path).get_content()
        # The file name loses the umlauts; the greeting must not.
        assert "Hi Björn Sjöberg," in body

    def test_no_raw_separator_leaks_into_the_greeting(self, drafted):
        for p in (drafted.outputs / "drafts" / "W01").glob("*.eml"):
            greeting = load_eml(p).get_content().splitlines()[0]
            assert "\t" not in greeting
            assert greeting.startswith("Hi ") and greeting.endswith(",")

    def test_score_matches_the_summary(self, drafted):
        summary = pd.read_csv(drafted.outputs / "weekly_summary_W01.csv")
        bjorn = summary[summary["PresenterChoice"].str.contains("Björn")].iloc[0]
        body = load_eml(drafted.outputs / "drafts" / "W01" / "Bjrn_Sjberg.eml").get_content()
        assert f"{bjorn['mean_score']:.2f} / 5.00" in body
        assert f"Number of reviewers: {int(bjorn['n_raters'])}" in body

    def test_comments_are_included(self, drafted):
        body = load_eml(drafted.outputs / "drafts" / "W01" / "Bjrn_Sjberg.eml").get_content()
        assert "clear and calm" in body

    def test_absent_comments_are_stated(self, drafted):
        body = load_eml(drafted.outputs / "drafts" / "W01" / "Carlos_Diaz_Ruiz.eml").get_content()
        assert "no written comments submitted" in body

    def test_txt_and_eml_agree(self, drafted):
        txt = (drafted.outputs / "mails" / "W01" / "Bjrn_Sjberg.txt").read_text(encoding="utf-8")
        eml_body = load_eml(drafted.outputs / "drafts" / "W01" / "Bjrn_Sjberg.eml").get_content()
        assert txt.startswith("Subject: Peer feedback summary (W01)")
        # Same body, one carries the subject as a header instead of a line.
        assert txt.split("\n\n", 1)[1].strip() == eml_body.strip()


class TestUnresolvedPresenters:
    def test_unknown_presenter_gets_txt_but_no_eml(self, artifact_dir, compute):
        responses = [
            Response(1, "W01", ANA, "Ghost\tStudent", uniform(4)),
            Response(2, "W01", ANA, presenter_choice(CARLOS), uniform(4)),
        ]
        root = make_root(artifact_dir / "PeerGrading", responses)
        compute(root.path)

        unresolved = pd.read_csv(root.outputs / "unresolved_presenters.csv")
        assert list(unresolved["PresenterChoice"]) == ["Ghost Student"]

        assert (root.outputs / "mails" / "W01" / "Ghost_Student.txt").exists()
        assert not (root.outputs / "drafts" / "W01" / "Ghost_Student.eml").exists()
        # The resolvable one is unaffected.
        assert (root.outputs / "drafts" / "W01" / "Carlos_Diaz_Ruiz.eml").exists()

    def test_file_is_empty_when_everyone_resolves(self, drafted):
        unresolved = pd.read_csv(drafted.outputs / "unresolved_presenters.csv")
        assert len(unresolved) == 0
