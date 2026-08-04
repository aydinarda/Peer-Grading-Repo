"""Shared fixtures.

Every integration test runs the real scripts as subprocesses against a data
folder under `artifacts/`, so after a test run you can open the generated CSVs
and .eml files and look at them. `artifacts/` is wiped at the start of each
session and is gitignored.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
ARTIFACTS = REPO / "artifacts"

# So tests can import the scripts directly for unit-level checks.
sys.path.insert(0, str(SCRIPTS))

EXIT_EXPIRED = 2


@pytest.fixture(scope="session", autouse=True)
def _fresh_artifacts():
    if ARTIFACTS.exists():
        shutil.rmtree(ARTIFACTS)
    ARTIFACTS.mkdir(parents=True)
    yield
    # Left in place on purpose - the point is to be able to inspect them.


@pytest.fixture
def artifact_dir(request) -> Path:
    """A per-test directory under artifacts/, named after the test."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", request.node.name)[:80]
    path = ARTIFACTS / safe
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def run_script(name: str, root: Path, *args: str, expect: int = 0) -> subprocess.CompletedProcess:
    """Run one of the scripts against a data root and check its exit code."""
    cmd = [sys.executable, str(SCRIPTS / name), "--root", str(root), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != expect:
        raise AssertionError(
            f"{name} exited {proc.returncode}, expected {expect}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc


@pytest.fixture
def compute():
    """Run compute_weekly.py against a data root."""
    def _run(root: Path, *args: str, expect: int = 0):
        return run_script("compute_weekly.py", root, *args, expect=expect)
    return _run


@pytest.fixture
def guard():
    """Run semester_guard.py against a data root."""
    def _run(root: Path, *args: str, expect: int = 0):
        return run_script("semester_guard.py", root, *args, expect=expect)
    return _run
