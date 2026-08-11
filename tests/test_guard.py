"""The semester guard: expiry, archiving, and the closure notice."""

from __future__ import annotations

import email
from datetime import datetime, timedelta
from email import policy

import pandas as pd
import pytest

import factories as f
from conftest import EXIT_EXPIRED
from factories import Response, make_root, presenter_choice, uniform

ANA, BJORN, CARLOS = f.ROSTER[:3]

YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
NEXT_YEAR = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")


def base_responses():
    return [
        Response(1, "W01", ANA, presenter_choice(CARLOS), uniform(5), "well done"),
        Response(2, "W01", BJORN, presenter_choice(CARLOS), uniform(4)),
    ]


class TestActiveSemester:
    def test_exit_zero_while_running(self, artifact_dir, guard):
        root = make_root(artifact_dir / "PeerGrading", base_responses(), semester_end=NEXT_YEAR)
        proc = guard(root.path, expect=0)
        assert "is active" in proc.stdout

    def test_nothing_is_archived(self, artifact_dir, guard):
        root = make_root(artifact_dir / "PeerGrading", base_responses(), semester_end=NEXT_YEAR)
        guard(root.path, expect=0)
        assert not root.archive.exists()


class TestMissingConfig:
    def test_refuses_without_semester_csv(self, artifact_dir, guard):
        root = make_root(artifact_dir / "PeerGrading", base_responses(), semester_end=NEXT_YEAR)
        (root.inputs / "semester.csv").unlink()
        proc = guard(root.path, expect=1)
        assert "semester.csv not found" in proc.stdout

    def test_refuses_without_required_key(self, artifact_dir, guard):
        root = make_root(artifact_dir / "PeerGrading", base_responses(), semester_end=NEXT_YEAR)
        (root.inputs / "semester.csv").write_text("key,value\nsemester_id,X\n", encoding="utf-8")
        proc = guard(root.path, expect=1)
        assert "missing a value" in proc.stdout


class TestExpiry:
    def test_stops_after_semester_end(self, artifact_dir, guard, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses(), semester_end=NEXT_YEAR)
        compute(root.path)

        _expire(root)
        proc = guard(root.path, expect=EXIT_EXPIRED)
        assert "not computing" in proc.stdout

    def test_per_file_override_expires_early(self, artifact_dir, guard):
        root = make_root(
            artifact_dir / "PeerGrading", base_responses(),
            semester_end=NEXT_YEAR,
            semester_extra={"expires:forms_responses.xlsx": YESTERDAY},
        )
        proc = guard(root.path, expect=EXIT_EXPIRED)
        assert "Expired input: forms_responses.xlsx" in proc.stdout
        # The semester itself has not ended, and the message must say so.
        assert "runs until" in proc.stdout


class TestArchive:
    @pytest.fixture
    def archived(self, artifact_dir, guard, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses(), semester_end=NEXT_YEAR)
        compute(root.path)
        _expire(root)
        guard(root.path, expect=EXIT_EXPIRED)
        return root, root.archive / "2026-TEST"

    def test_inputs_are_snapshotted(self, archived):
        """The whole Input tree, so the archive explains its own numbers."""
        _, archive_dir = archived
        names = {p.name for p in (archive_dir / "Input").rglob("*") if p.is_file()}
        assert {
            "forms_responses.xlsx", "StudentListDB.xlsx", "week_windows.csv",
            "semester.csv", "rubric.csv", "mail_template.txt",
        } <= names

    def test_input_layout_is_preserved(self, archived):
        _, archive_dir = archived
        assert (archive_dir / "Input" / "form_exports" / "forms_responses.xlsx").is_file()
        assert (archive_dir / "Input" / "week_setup" / "week_windows.csv").is_file()

    def test_outputs_are_copied(self, archived):
        root, archive_dir = archived
        archived_files = {p.name for p in (archive_dir / "Output").rglob("*") if p.is_file()}
        # The closure marker is written after archiving, and is bookkeeping
        # rather than a result, so it is not expected inside the archive.
        live_files = {
            p.name for p in root.outputs.rglob("*")
            if p.is_file() and p.name != "semester_closed.txt"
        }
        assert live_files <= archived_files

    def test_drafts_are_kept(self, archived):
        _, archive_dir = archived
        assert list((archive_dir / "Output" / "W01" / "drafts").glob("*.eml"))

    def test_manifest_records_the_semester(self, archived):
        _, archive_dir = archived
        manifest = (archive_dir / "MANIFEST.txt").read_text(encoding="utf-8")
        assert "semester_id: 2026-TEST" in manifest
        assert "Week-by-week status" in manifest
        assert "W01/summary.csv" in manifest

    def test_closure_notice_is_written(self, archived):
        _, archive_dir = archived
        notice = (archive_dir / "SEMESTER_CLOSED.txt").read_text(encoding="utf-8")
        assert "has ended" in notice
        assert "Archive" in notice

    def test_closure_draft_is_addressed_to_the_ta(self, archived):
        _, archive_dir = archived
        msg = email.message_from_bytes(
            (archive_dir / "semester_closed.eml").read_bytes(), policy=policy.default
        )
        assert msg["To"] == "ta@example.edu"
        assert msg["From"] is None
        assert "2026-TEST" in msg["Subject"]


class TestIdempotency:
    def test_second_run_is_silent(self, artifact_dir, guard, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses(), semester_end=NEXT_YEAR)
        compute(root.path)
        _expire(root)

        first = guard(root.path, expect=EXIT_EXPIRED)
        assert "Archiving semester" in first.stdout

        second = guard(root.path, expect=EXIT_EXPIRED)
        assert "already recorded" in second.stdout
        assert "Archiving semester" not in second.stdout

    def test_marker_records_the_closure(self, artifact_dir, guard, compute):
        root = make_root(artifact_dir / "PeerGrading", base_responses(), semester_end=NEXT_YEAR)
        compute(root.path)
        _expire(root)
        guard(root.path, expect=EXIT_EXPIRED)

        marker = (root.outputs / "semester_closed.txt").read_text(encoding="utf-8")
        assert "semester_id=2026-TEST" in marker
        assert "notified=ta@example.edu" in marker

    def test_failed_archive_leaves_no_marker(self, artifact_dir, guard, compute, monkeypatch):
        """If the archive cannot be written the closure must be retried, not recorded."""
        root = make_root(artifact_dir / "PeerGrading", base_responses(), semester_end=NEXT_YEAR)
        compute(root.path)
        _expire(root)

        # A file where the Archive directory needs to be makes mkdir fail.
        root.archive.write_text("not a directory", encoding="utf-8")

        guard(root.path, expect=1)
        assert not (root.outputs / "semester_closed.txt").exists()


def _expire(root) -> None:
    """Move semester_end into the past, leaving everything else alone."""
    df = pd.read_csv(root.inputs / "semester.csv")
    df.loc[df["key"] == "semester_end", "value"] = YESTERDAY
    df.to_csv(root.inputs / "semester.csv", index=False)
