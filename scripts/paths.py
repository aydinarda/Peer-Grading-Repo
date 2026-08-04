#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Where the PeerGrading data lives.

Every path in the system is derived from one base directory: the SharePoint
library synced to this machine by the OneDrive client. Because that path differs
per machine and per OS, it is never committed - it is resolved at runtime, in
this order:

  1. an explicit --root argument
  2. the PEERGRADING_ROOT environment variable
  3. .peergrading.json in the repo root, written by scripts/setup_local.py

This module is also the seam that keeps the storage decision cheap to revisit.
Everything else asks for `inputs()` or `outputs()` and never learns where the
folder actually came from, so swapping OneDrive sync for the Graph API later
means rewriting this file and nothing else.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = REPO_ROOT / ".peergrading.json"

# Marker used to recognise a valid data root, both here and in setup_local.py.
MARKER = Path("Input") / "week_setup" / "week_windows.csv"

_override: Path | None = None


def set_root(path: str | Path | None) -> None:
    """Record the --root argument, if one was given."""
    global _override
    _override = Path(path).expanduser() if path else None


def add_root_argument(parser) -> None:
    """Attach the shared --root flag to an argparse parser."""
    parser.add_argument(
        "--root",
        default=None,
        help="PeerGrading data folder (the synced SharePoint library). "
             "Overrides PEERGRADING_ROOT and .peergrading.json.",
    )


def load_configured_root() -> Path | None:
    if not CONFIG_FILE.exists():
        return None
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"ERROR: {CONFIG_FILE} is not valid JSON. Re-run scripts/setup_local.py.")
        sys.exit(1)
    value = str(data.get("root", "")).strip()
    return Path(value).expanduser() if value else None


def root() -> Path:
    """Resolve the data root, or explain how to set one."""
    for candidate in (_override, _env_root(), load_configured_root()):
        if candidate:
            if not candidate.exists():
                print(f"ERROR: data folder does not exist: {candidate}")
                print("       If the SharePoint library moved, re-run scripts/setup_local.py.")
                sys.exit(1)
            return candidate

    print("ERROR: no PeerGrading data folder configured.")
    print("       Run: python3 scripts/setup_local.py")
    print("       Or pass --root /path/to/PeerGrading, or set PEERGRADING_ROOT.")
    sys.exit(1)


def _env_root() -> Path | None:
    value = os.getenv("PEERGRADING_ROOT", "").strip()
    return Path(value).expanduser() if value else None


def looks_like_root(path: Path) -> bool:
    """True when `path` holds a PeerGrading data folder."""
    return (path / MARKER).is_file()


# --- Standard locations -----------------------------------------------------

def inputs() -> Path:
    return root() / "Input"


def outputs() -> Path:
    return root() / "Output"


def archive() -> Path:
    return root() / "Archive"


def responses_file() -> Path:
    return inputs() / "form_exports" / "forms_responses.xlsx"


def students_file() -> Path:
    return inputs() / "students_db" / "StudentListDB.xlsx"


def week_windows_file() -> Path:
    return inputs() / "week_setup" / "week_windows.csv"


def semester_file() -> Path:
    return inputs() / "semester.csv"


def marker_file() -> Path:
    """Records that a semester has been closed, so it is only announced once."""
    return outputs() / "semester_closed.txt"


def tracked_inputs() -> dict[str, Path]:
    """Inputs the expiry guard reports on, keyed by their `expires:<name>` key."""
    return {
        "forms_responses.xlsx": responses_file(),
        "StudentListDB.xlsx": students_file(),
        "week_windows.csv": week_windows_file(),
    }
