"""Unit tests for the pure helpers, imported directly."""

from __future__ import annotations

import email
from datetime import datetime
from email import policy
from zoneinfo import ZoneInfo

import pytest

import compute_weekly as cw
import paths
import semester_config as sc

import factories as f


class TestLikert:
    @pytest.mark.parametrize("raw,expected", [
        ("Bad 1", 1), ("Poor 2", 2), ("Fair 3", 3), ("Good 4", 4), ("Excellent 5", 5),
        ("bad 1", 1), ("  Good 4  ", 4),
        ("Something else 3", 3),   # trailing digit wins
        ("4", 4), (4, 4), (4.0, 4),
    ])
    def test_parsed(self, raw, expected):
        assert cw.likert_to_int(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", None, "no digits", "9", "0"])
    def test_rejected(self, raw):
        assert cw.likert_to_int(raw) is None


class TestNameNormalisation:
    @pytest.mark.parametrize("raw,expected", [
        ("Ana\tLang", "Ana Lang"),
        ("Ana Lang\n", "Ana Lang"),
        ("  Ana   Lang  ", "Ana Lang"),
        ("Carlos\tDiaz Ruiz", "Carlos Diaz Ruiz"),
        ("Mei Ling\tChen", "Mei Ling Chen"),
        ("Björn\tSjöberg", "Björn Sjöberg"),
    ])
    def test_separators_collapse(self, raw, expected):
        assert cw.normalize_display_name(raw) == expected

    def test_empty_input(self):
        assert cw.normalize_display_name(None) == ""
        assert cw.normalize_display_name("") == ""

    def test_every_roster_name_resolves(self, tmp_path):
        """The dropdown values must all map back to a roster address."""
        roster_path = tmp_path / "roster.xlsx"
        f.write_roster(roster_path)
        students = cw.load_students(roster_path)
        mapping = cw.build_presenter_email_map(students)

        for student in f.ROSTER:
            for sep in ("\t", " ", "\n"):
                key = cw.normalize_display_name(f.presenter_choice(student, sep)).lower()
                assert mapping.get(key) == student.email, f"{student.full!r} with sep {sep!r}"

    def test_unknown_name_is_not_invented(self, tmp_path):
        roster_path = tmp_path / "roster.xlsx"
        f.write_roster(roster_path)
        mapping = cw.build_presenter_email_map(cw.load_students(roster_path))
        assert mapping.get("nobody at all") is None


class TestFilenames:
    @pytest.mark.parametrize("raw,expected", [
        ("Ana Lang", "Ana_Lang"),
        ("Carlos Diaz Ruiz", "Carlos_Diaz_Ruiz"),
        ("", "unknown"),
    ])
    def test_sanitised(self, raw, expected):
        assert cw.sanitize_filename(raw) == expected

    def test_non_ascii_is_stripped_from_the_filename_only(self):
        # The file name loses the umlaut; the mail body and To: header keep it.
        assert cw.sanitize_filename("Björn Sjöberg") == "Bjrn_Sjberg"


class TestEml:
    def test_headers_and_body(self, tmp_path):
        path = tmp_path / "draft.eml"
        cw.write_eml(path, to_email="ana.lang@example.edu",
                     subject="Peer feedback summary (W01)",
                     body="Hi Björn Sjöberg,\n\nScore: 4.20\n")

        msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
        assert msg["To"] == "ana.lang@example.edu"
        assert msg["Subject"] == "Peer feedback summary (W01)"
        assert msg["Date"]
        # No From on purpose: Outlook fills in the account the draft lands in.
        assert msg["From"] is None
        assert "Björn Sjöberg" in msg.get_content()


class TestDeadlineParsing:
    tz = ZoneInfo("Europe/Zurich")

    def test_bare_date_covers_the_whole_day(self):
        dt = sc.parse_deadline("2026-06-14", self.tz, "semester_end")
        assert (dt.hour, dt.minute, dt.second) == (23, 59, 59)
        assert dt.date() == datetime(2026, 6, 14).date()

    def test_explicit_time_is_kept(self):
        dt = sc.parse_deadline("2026-06-14 08:30", self.tz, "semester_end")
        assert (dt.hour, dt.minute) == (8, 30)

    def test_slashes_accepted(self):
        assert sc.parse_deadline("2026/06/14", self.tz, "x").date() == datetime(2026, 6, 14).date()

    def test_garbage_exits(self):
        with pytest.raises(SystemExit):
            sc.parse_deadline("not a date", self.tz, "semester_end")


class TestRootDetection:
    def test_marker_identifies_a_root(self, tmp_path):
        assert not paths.looks_like_root(tmp_path)
        (tmp_path / "Input" / "week_setup").mkdir(parents=True)
        (tmp_path / "Input" / "week_setup" / "week_windows.csv").write_text("week_id\n")
        assert paths.looks_like_root(tmp_path)
