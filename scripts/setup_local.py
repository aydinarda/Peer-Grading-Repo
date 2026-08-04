#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-time setup: point this checkout at the synced SharePoint folder.

Run once per machine, and again if the folder ever moves:

    python3 scripts/setup_local.py

It looks for the PeerGrading library in the usual OneDrive sync locations on
macOS and Windows, confirms what it found, and writes the path to
.peergrading.json. If nothing turns up it asks for the path instead.

Nothing here needs a password, a token or a network connection - the folder is
already on disk, put there by the OneDrive client.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import paths

# Depth to descend under each search base. The library is usually
# <base>/<Tenant - Site>/PeerGrading, so three levels is plenty.
MAX_DEPTH = 3


def search_bases() -> list[Path]:
    """Where the OneDrive client tends to put synced libraries."""
    home = Path.home()
    bases: list[Path] = []

    if sys.platform == "darwin":
        bases.append(home / "Library" / "CloudStorage")
    elif sys.platform.startswith("win"):
        import os
        for var in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
            value = os.getenv(var, "").strip()
            if value:
                bases.append(Path(value))
        bases.append(home)
    else:
        bases.append(home)

    bases.append(home / "OneDrive")
    return [b for b in dict.fromkeys(bases) if b.is_dir()]


def find_candidates() -> list[Path]:
    found: list[Path] = []
    for base in search_bases():
        for candidate in _walk(base, MAX_DEPTH):
            if paths.looks_like_root(candidate) and candidate not in found:
                found.append(candidate)
    return found


def _walk(base: Path, depth: int):
    """Yield directories under `base`, breadth-first, up to `depth` levels."""
    level = [base]
    for _ in range(depth + 1):
        nxt = []
        for directory in level:
            try:
                children = [c for c in directory.iterdir() if c.is_dir()]
            except (PermissionError, OSError):
                continue
            for child in children:
                yield child
                nxt.append(child)
        level = nxt
        if not level:
            return


def prompt_for_root() -> Path | None:
    print("\nEnter the full path to the PeerGrading folder (or press Enter to give up).")
    print("On macOS you can drag the folder from Finder into this window.")
    raw = input("Path: ").strip().strip("'\"")
    if not raw:
        return None
    return Path(raw).expanduser()


def save(root: Path) -> None:
    paths.CONFIG_FILE.write_text(
        json.dumps({"root": str(root)}, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nSaved to {paths.CONFIG_FILE}")


def main() -> None:
    print("Looking for the synced PeerGrading folder...")
    candidates = find_candidates()

    chosen: Path | None = None
    if len(candidates) == 1:
        chosen = candidates[0]
        print(f"Found: {chosen}")
    elif len(candidates) > 1:
        print("Found more than one:")
        for i, c in enumerate(candidates, 1):
            print(f"  {i}. {c}")
        raw = input(f"Which one? [1-{len(candidates)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(candidates):
            chosen = candidates[int(raw) - 1]
        else:
            print("Not a valid choice.")
            sys.exit(1)
    else:
        print("Could not find it automatically.")
        print("Make sure the SharePoint library is synced ('Add shortcut to OneDrive')")
        print("and that it contains Input/week_setup/week_windows.csv.")
        chosen = prompt_for_root()

    if chosen is None:
        print("Nothing configured. Re-run this script once the folder is synced.")
        sys.exit(1)

    if not chosen.is_dir():
        print(f"ERROR: not a folder: {chosen}")
        sys.exit(1)

    if not paths.looks_like_root(chosen):
        print(f"ERROR: {chosen} does not look like a PeerGrading folder.")
        print(f"       Expected to find {paths.MARKER} inside it.")
        sys.exit(1)

    save(chosen)

    print("\nContents:")
    for name in ("Input", "Output", "Archive"):
        sub = chosen / name
        print(f"  {name}/  {'ok' if sub.is_dir() else '(will be created on first run)'}")

    print("\nDone. Next: run the weekly computation with")
    print("  ./run.sh            (macOS)")
    print("  .\\run.ps1           (Windows)")


if __name__ == "__main__":
    main()
