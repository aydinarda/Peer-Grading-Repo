# Weekly PeerGrading run (Windows).
#
#   .\run.ps1              compute every week
#   .\run.ps1 --week W04   compute one week
#
# Creates the virtualenv and installs dependencies on first use, so a fresh
# machine needs nothing but Python.
$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

$venv = ".venv"
if (-not (Test-Path $venv)) {
    Write-Host "Creating virtualenv..."
    python -m venv $venv
    & "$venv\Scripts\pip.exe" install --quiet --upgrade pip
    & "$venv\Scripts\pip.exe" install --quiet -r requirements.txt
}

$py = "$venv\Scripts\python.exe"

if ((-not (Test-Path ".peergrading.json")) -and (-not $env:PEERGRADING_ROOT)) {
    Write-Host "No data folder configured yet. Running setup..."
    & $py scripts\setup_local.py
}

# The guard decides whether we are still inside the semester.
#   0 = active, 2 = expired (stop quietly), anything else = real error.
& $py scripts\semester_guard.py
$guardStatus = $LASTEXITCODE

switch ($guardStatus) {
    0 { }
    2 { Write-Host "Semester is over - nothing to compute."; exit 0 }
    default { Write-Host "Guard failed; not computing."; exit $guardStatus }
}

& $py scripts\compute_weekly.py @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Done. Draft mails are under Output\<week>\drafts\ in the PeerGrading folder."
Write-Host "Drag the .eml files into Outlook Drafts, review them, then send."
