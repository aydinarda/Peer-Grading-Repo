#!/usr/bin/env bash
# Weekly PeerGrading run (macOS / Linux).
#
#   ./run.sh              compute every week
#   ./run.sh --week W04   compute one week
#
# Creates the virtualenv and installs dependencies on first use, so a fresh
# machine needs nothing but Python.
set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
if [ ! -d "$VENV" ]; then
  echo "Creating virtualenv..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r requirements.txt
fi

PY="$VENV/bin/python"

if [ ! -f ".peergrading.json" ] && [ -z "${PEERGRADING_ROOT:-}" ]; then
  echo "No data folder configured yet. Running setup..."
  "$PY" scripts/setup_local.py
fi

# The guard decides whether we are still inside the semester.
#   0 = active, 2 = expired (stop quietly), anything else = real error.
set +e
"$PY" scripts/semester_guard.py
guard_status=$?
set -e

case "$guard_status" in
  0) ;;
  2) echo "Semester is over - nothing to compute."; exit 0 ;;
  *) echo "Guard failed; not computing."; exit "$guard_status" ;;
esac

"$PY" scripts/compute_weekly.py "$@"

echo
echo "Done. Draft mails are under Output/<week>/drafts/ in the PeerGrading folder."
echo "Drag the .eml files into Outlook Drafts, review them, then send."
