"""Proof that the system cannot contact anything.

The tool used to fetch inputs over IMAP and mail results back out. It no longer
does, and that is a property worth pinning down rather than trusting: these tests
make any outbound connection raise, then run the full pipeline.

They also guard against the obvious regression - someone adding a "just send the
mails automatically" step - because the drafts are supposed to be reviewed by a
person before they reach students.
"""

from __future__ import annotations

import socket
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import factories as f
from factories import Response, make_root, presenter_choice, uniform

ANA, BJORN, CARLOS = f.ROSTER[:3]
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


@pytest.fixture
def no_network(monkeypatch):
    """Make every socket connection attempt fail loudly."""
    def blocked(*args, **kwargs):
        raise AssertionError("the pipeline attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def _run_in_process(script: str, root: Path, argv: list[str] | None = None) -> int:
    """Run a script inside this interpreter, so monkeypatching applies."""
    import runpy

    saved = sys.argv[:]
    sys.argv = [str(SCRIPTS / script), "--root", str(root), *(argv or [])]
    try:
        runpy.run_path(str(SCRIPTS / script), run_name="__main__")
        return 0
    except SystemExit as exc:
        return exc.code or 0
    finally:
        sys.argv = saved


class TestOfflineOperation:
    def test_compute_needs_no_network(self, artifact_dir, no_network):
        responses = [
            Response(1, "W01", ANA, presenter_choice(CARLOS), uniform(5), "good"),
            Response(2, "W01", BJORN, presenter_choice(CARLOS), uniform(3)),
        ]
        root = make_root(artifact_dir / "PeerGrading", responses)

        assert _run_in_process("compute_weekly.py", root.path) == 0
        assert (root.drafts("W01") / "Carlos_Diaz_Ruiz.eml").exists()

    def test_guard_needs_no_network(self, artifact_dir, no_network):
        responses = [Response(1, "W01", ANA, presenter_choice(CARLOS), uniform(4))]
        next_year = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")
        root = make_root(artifact_dir / "PeerGrading", responses, semester_end=next_year)

        assert _run_in_process("semester_guard.py", root.path) == 0

    def test_closing_a_semester_needs_no_network(self, artifact_dir, no_network):
        """Archiving and the closure notice are local file writes, not mail."""
        responses = [Response(1, "W01", ANA, presenter_choice(CARLOS), uniform(4))]
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        root = make_root(artifact_dir / "PeerGrading", responses, semester_end=yesterday)

        assert _run_in_process("semester_guard.py", root.path) == 2
        assert (root.archive / "2026-TEST" / "semester_closed.eml").exists()


class TestNoMailMachinery:
    """The scripts must not gain the ability to send mail again by accident."""

    @pytest.mark.parametrize("script", [
        "compute_weekly.py", "semester_guard.py", "semester_config.py",
        "paths.py", "setup_local.py",
    ])
    def test_no_mail_transport_imports(self, script):
        source = (SCRIPTS / script).read_text(encoding="utf-8")
        for forbidden in ("smtplib", "imaplib", "import requests", "urllib.request"):
            assert forbidden not in source, f"{script} pulls in {forbidden}"

    def test_no_credentials_are_read(self):
        """Nothing should be looking for a password or token in the environment."""
        for script in SCRIPTS.glob("*.py"):
            source = script.read_text(encoding="utf-8")
            for forbidden in ("MAIL_", "PASSWORD", "_TOKEN", "CLIENT_SECRET"):
                assert forbidden not in source, f"{script.name} references {forbidden}"
