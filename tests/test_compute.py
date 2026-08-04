"""End-to-end behaviour of a single compute run."""

from __future__ import annotations

import pandas as pd
import pytest

import factories as f
from factories import Response, make_root, presenter_choice, scores, uniform

ANA, BJORN, CARLOS, DANA, EMIL = f.ROSTER[:5]


def read(path):
    return pd.read_csv(path)


@pytest.fixture
def basic_root(artifact_dir):
    """W01 and W02 have grades; W03 and W04 are past but empty; W05/W06 future."""
    responses = [
        Response(1, "W01", ANA, presenter_choice(CARLOS), uniform(5), "great"),
        Response(2, "W01", BJORN, presenter_choice(CARLOS), uniform(3), "ok"),
        Response(3, "W01", DANA, presenter_choice(BJORN, sep=" "), uniform(4)),
        Response(4, "W02", ANA, presenter_choice(EMIL, sep="\n"), scores(1, 2, 3, 4, 5, 4, 2)),
    ]
    return make_root(artifact_dir / "PeerGrading", responses)


class TestScoring:
    def test_weighted_formula(self, basic_root, compute):
        compute(basic_root.path)
        summary = read(basic_root.outputs / "weekly_summary_W02.csv")
        row = summary.iloc[0]
        # 0.1*(1+2+3+4+5+4) + 0.4*2 = 1.9 + 0.8 = 2.7
        assert row["mean_score"] == pytest.approx(2.7)

    def test_mean_across_raters(self, basic_root, compute):
        compute(basic_root.path)
        summary = read(basic_root.outputs / "weekly_summary_W01.csv")
        carlos = summary[summary["PresenterChoice"].str.contains("Carlos")].iloc[0]
        assert carlos["n_raters"] == 2
        assert carlos["mean_score"] == pytest.approx(4.0)   # (5.0 + 3.0) / 2

    def test_incomplete_response_is_dropped(self, artifact_dir, compute):
        """A response missing one answer must not produce a partial score."""
        responses = [
            Response(1, "W01", ANA, presenter_choice(CARLOS), uniform(5)),
            Response(2, "W01", BJORN, presenter_choice(CARLOS), uniform(4)),
        ]
        root = make_root(artifact_dir / "PeerGrading", responses)

        df = pd.read_excel(root.forms)
        df.loc[df["ResponseId"] == 2, "Q3_2"] = "no answer here"
        df.to_excel(root.forms, index=False)

        compute(root.path)
        summary = read(root.outputs / "weekly_summary_W01.csv")
        assert summary.iloc[0]["n_raters"] == 1


class TestDeduplication:
    def test_same_rater_twice_keeps_the_later_one(self, artifact_dir, compute):
        responses = [
            Response(10, "W01", ANA, presenter_choice(CARLOS), uniform(1), hour_offset=10),
            Response(11, "W01", ANA, presenter_choice(CARLOS), uniform(5), hour_offset=20),
        ]
        root = make_root(artifact_dir / "PeerGrading", responses)
        compute(root.path)

        summary = read(root.outputs / "weekly_summary_W01.csv")
        assert summary.iloc[0]["n_raters"] == 1
        assert summary.iloc[0]["mean_score"] == pytest.approx(5.0)

    def test_different_raters_both_count(self, basic_root, compute):
        compute(basic_root.path)
        log = read(basic_root.outputs / "peer_log_W01.csv")
        carlos_rows = log[log["PresenterChoice"].str.contains("Carlos")]
        assert set(carlos_rows["RaterEmail"]) == {ANA.email, BJORN.email}


class TestWeekAssignment:
    def test_response_after_deadline_is_excluded(self, artifact_dir, compute):
        # hour_offset beyond the 7-day window pushes it past the deadline.
        responses = [
            Response(1, "W01", ANA, presenter_choice(CARLOS), uniform(5)),
            Response(2, "W01", BJORN, presenter_choice(CARLOS), uniform(5), hour_offset=24 * 9),
        ]
        root = make_root(artifact_dir / "PeerGrading", responses)
        compute(root.path)
        assert read(root.outputs / "weekly_summary_W01.csv").iloc[0]["n_raters"] == 1

    def test_unresolved_timestamp_is_dropped(self, artifact_dir, compute):
        """The real export contains a literal `utcNow()` that never resolved."""
        responses = [
            Response(1, "W01", ANA, presenter_choice(CARLOS), uniform(5), raw_received="utcNow()"),
            Response(2, "W01", BJORN, presenter_choice(CARLOS), uniform(4)),
        ]
        root = make_root(artifact_dir / "PeerGrading", responses)
        compute(root.path)
        assert read(root.outputs / "weekly_summary_W01.csv").iloc[0]["n_raters"] == 1


class TestWeeklyStatus:
    def test_every_defined_week_has_a_row(self, basic_root, compute):
        compute(basic_root.path)
        status = read(basic_root.outputs / "weekly_status.csv")
        assert list(status["week_id"]) == [w["week_id"] for w in basic_root.windows]

    def test_statuses(self, basic_root, compute):
        compute(basic_root.path)
        status = read(basic_root.outputs / "weekly_status.csv").set_index("week_id")

        assert status.loc["W01", "status"] == "computed"
        assert status.loc["W02", "status"] == "computed"
        assert status.loc["W03", "status"] == "no_data"   # window passed, nothing came in
        assert status.loc["W04", "status"] == "no_data"
        assert status.loc["W05", "status"] == "pending"   # window has not opened
        assert status.loc["W06", "status"] == "pending"

    def test_counts(self, basic_root, compute):
        compute(basic_root.path)
        status = read(basic_root.outputs / "weekly_status.csv").set_index("week_id")
        assert status.loc["W01", "n_responses"] == 3
        assert status.loc["W01", "n_presenters"] == 2
        assert status.loc["W03", "n_responses"] == 0


class TestEmptyWeeks:
    def test_empty_week_files_exist_with_headers(self, basic_root, compute):
        compute(basic_root.path)
        summary = read(basic_root.outputs / "weekly_summary_W03.csv")
        assert len(summary) == 0
        assert "mean_score" in summary.columns

        log = read(basic_root.outputs / "peer_log_W03.csv")
        assert len(log) == 0

    def test_empty_week_attendance_lists_whole_roster_at_zero(self, basic_root, compute):
        compute(basic_root.path)
        att = read(basic_root.outputs / "attendance_W03.csv")
        assert len(att) == len(f.ROSTER)
        assert att["submitted"].sum() == 0
        assert att["n_submissions"].sum() == 0

    def test_pending_week_produces_no_files(self, basic_root, compute):
        compute(basic_root.path)
        assert not (basic_root.outputs / "weekly_summary_W05.csv").exists()
        assert not (basic_root.outputs / "attendance_W05.csv").exists()

    def test_no_mail_folder_for_empty_week(self, basic_root, compute):
        compute(basic_root.path)
        assert not (basic_root.outputs / "mails" / "W03").exists()
        assert not (basic_root.outputs / "drafts" / "W03").exists()

    def test_everything_empty_still_reports(self, artifact_dir, compute):
        """No grades at all anywhere is a result, not a crash."""
        root = make_root(artifact_dir / "PeerGrading", [])
        compute(root.path)
        status = read(root.outputs / "weekly_status.csv")
        assert len(status) == 6
        assert set(status[status["status"] != "pending"]["status"]) == {"no_data"}


class TestAttendance:
    def test_submitters_are_marked(self, basic_root, compute):
        compute(basic_root.path)
        att = read(basic_root.outputs / "attendance_W01.csv").set_index("StudentEmail")
        assert att.loc[ANA.email, "submitted"] == 1
        assert att.loc[EMIL.email, "submitted"] == 0

    def test_roster_is_complete(self, basic_root, compute):
        compute(basic_root.path)
        att = read(basic_root.outputs / "attendance_W01.csv")
        assert set(att["StudentEmail"]) == {s.email for s in f.ROSTER}


class TestWeekFilter:
    def test_single_week_only_touches_that_week(self, basic_root, compute):
        compute(basic_root.path, "--week", "W01")
        status = read(basic_root.outputs / "weekly_status.csv")
        assert list(status["week_id"]) == ["W01"]
        assert not (basic_root.outputs / "weekly_summary_W02.csv").exists()
