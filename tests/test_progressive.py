"""The real usage pattern: the export grows week by week and is recomputed.

Each week the Forms export contains everything submitted so far, and the tool
runs again over the whole thing. Two properties matter across those runs:

  - a week that has already been computed must not change when later weeks
    arrive (last week's grades are already in someone's inbox);
  - master/grade_edges.csv accumulates without duplicating, because attendance
    counts are derived from it.
"""

from __future__ import annotations

import pandas as pd
import pytest

import factories as f
from factories import Response, make_root, presenter_choice, scores, uniform

ANA, BJORN, CARLOS, DANA, EMIL, FARIDA = f.ROSTER[:6]


def semester_responses() -> dict[str, list[Response]]:
    """Four past weeks; W03 is a week nobody submitted anything for."""
    return {
        "W01": [
            Response(101, "W01", ANA, presenter_choice(CARLOS), uniform(5), "strong opener"),
            Response(102, "W01", BJORN, presenter_choice(CARLOS), scores(4, 4, 5, 4, 3, 4, 4)),
            Response(103, "W01", DANA, presenter_choice(EMIL), uniform(3)),
        ],
        "W02": [
            Response(201, "W02", ANA, presenter_choice(BJORN, sep=" "), uniform(4)),
            Response(202, "W02", CARLOS, presenter_choice(BJORN, sep=" "), scores(5, 4, 4, 5, 5, 4, 5)),
        ],
        "W03": [],
        "W04": [
            Response(401, "W04", EMIL, presenter_choice(FARIDA, sep="\n"), uniform(2), "rushed"),
            Response(402, "W04", ANA, presenter_choice(FARIDA, sep="\n"), scores(3, 3, 2, 3, 2, 3, 3)),
            Response(403, "W04", BJORN, presenter_choice(DANA), uniform(5)),
        ],
    }


def cumulative_upto(weeks: dict[str, list[Response]], upto: str) -> list[Response]:
    out: list[Response] = []
    for week, responses in weeks.items():
        out.extend(responses)
        if week == upto:
            break
    return out


@pytest.fixture
def semester(artifact_dir):
    """A data folder seeded with W01 only; later weeks get added by the test."""
    weeks = semester_responses()
    root = make_root(artifact_dir / "PeerGrading", cumulative_upto(weeks, "W01"))
    root.weeks = weeks
    return root


class TestWeekByWeek:
    def test_status_advances_as_weeks_arrive(self, semester, compute):
        expectations = {
            "W01": {"W01": "computed", "W02": "no_data", "W03": "no_data", "W04": "no_data"},
            "W02": {"W01": "computed", "W02": "computed", "W03": "no_data", "W04": "no_data"},
            "W03": {"W01": "computed", "W02": "computed", "W03": "no_data", "W04": "no_data"},
            "W04": {"W01": "computed", "W02": "computed", "W03": "no_data", "W04": "computed"},
        }
        for week, expected in expectations.items():
            semester.rewrite_forms(cumulative_upto(semester.weeks, week))
            compute(semester.path)
            status = pd.read_csv(semester.outputs / "weekly_status.csv").set_index("week_id")
            for week_id, want in expected.items():
                assert status.loc[week_id, "status"] == want, f"after {week}: {week_id}"

    def test_earlier_weeks_stay_frozen(self, semester, compute):
        """Recomputing after week 4 must not alter what week 1 said."""
        compute(semester.path)
        w01_first = (semester.outputs / "weekly_summary_W01.csv").read_text()
        drafts_first = sorted(p.name for p in (semester.outputs / "drafts" / "W01").glob("*.eml"))

        for week in ("W02", "W03", "W04"):
            semester.rewrite_forms(cumulative_upto(semester.weeks, week))
            compute(semester.path)

        assert (semester.outputs / "weekly_summary_W01.csv").read_text() == w01_first
        assert sorted(p.name for p in (semester.outputs / "drafts" / "W01").glob("*.eml")) == drafts_first

    def test_each_weeks_drafts_appear_in_their_own_folder(self, semester, compute):
        for week in ("W01", "W02", "W03", "W04"):
            semester.rewrite_forms(cumulative_upto(semester.weeks, week))
            compute(semester.path)

        counts = {
            week: len(list((semester.outputs / "drafts" / week).glob("*.eml")))
            for week in ("W01", "W02", "W04")
        }
        assert counts == {"W01": 2, "W02": 1, "W04": 2}
        assert not (semester.outputs / "drafts" / "W03").exists()


class TestMasterAccumulation:
    def test_edges_cover_every_week_seen(self, semester, compute):
        for week in ("W01", "W02", "W03", "W04"):
            semester.rewrite_forms(cumulative_upto(semester.weeks, week))
            compute(semester.path)

        edges = pd.read_csv(semester.outputs / "master" / "grade_edges.csv")
        assert set(edges["week_id"]) == {"W01", "W02", "W04"}

    @pytest.mark.xfail(
        strict=True,
        reason="Known bug: grade_edges.csv is re-read with ResponseId as int64 while the "
               "new rows carry it as str, so drop_duplicates never matches and every "
               "re-run appends the whole history again.",
    )
    def test_repeated_runs_do_not_duplicate_edges(self, semester, compute):
        responses = cumulative_upto(semester.weeks, "W04")
        semester.rewrite_forms(responses)

        compute(semester.path)
        after_one = len(pd.read_csv(semester.outputs / "master" / "grade_edges.csv"))

        compute(semester.path)
        after_two = len(pd.read_csv(semester.outputs / "master" / "grade_edges.csv"))

        assert after_two == after_one, (
            f"re-running the same export grew grade_edges from {after_one} to {after_two}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason="Same int64/str dedup bug: inflated edges inflate the submission counts.",
    )
    def test_attendance_counts_survive_a_rerun(self, semester, compute):
        semester.rewrite_forms(cumulative_upto(semester.weeks, "W01"))
        compute(semester.path)
        first = pd.read_csv(semester.outputs / "attendance_W01.csv").set_index("StudentEmail")

        compute(semester.path)
        second = pd.read_csv(semester.outputs / "attendance_W01.csv").set_index("StudentEmail")

        assert second.loc[ANA.email, "n_submissions"] == first.loc[ANA.email, "n_submissions"]


class TestDuplicateResponseIds:
    """The real export reuses one ResponseId across three different responses."""

    def test_reused_id_across_presenters_is_still_counted_once_each(self, artifact_dir, compute):
        responses = [
            Response(11, "W01", ANA, presenter_choice(CARLOS), uniform(5)),
            Response(11, "W01", ANA, presenter_choice(EMIL), uniform(4)),
        ]
        root = make_root(artifact_dir / "PeerGrading", responses)
        compute(root.path)

        summary = pd.read_csv(root.outputs / "weekly_summary_W01.csv")
        assert len(summary) == 2, "both presenters should appear despite the shared id"

    def test_reused_id_same_presenter_collapses_to_one(self, artifact_dir, compute):
        responses = [
            Response(11, "W01", ANA, presenter_choice(CARLOS), uniform(5), hour_offset=10),
            Response(11, "W01", ANA, presenter_choice(CARLOS), uniform(4), hour_offset=11),
        ]
        root = make_root(artifact_dir / "PeerGrading", responses)
        compute(root.path)

        summary = pd.read_csv(root.outputs / "weekly_summary_W01.csv")
        assert summary.iloc[0]["n_raters"] == 1
        assert summary.iloc[0]["mean_score"] == pytest.approx(4.0)   # the later one wins
