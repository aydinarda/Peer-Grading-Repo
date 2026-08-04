# Running it automatically every week

Both schedulers just call `run.sh` / `run.ps1`. Nothing else is different from a
manual run, so if the schedule ever breaks you can always fall back to running
the command yourself.

Note that the machine has to be awake and the SharePoint folder synced at the
scheduled time. Missing a week costs nothing: the next run recomputes everything
from scratch, because all the state lives in the SharePoint folder.

## macOS (launchd)

1. Edit `com.peergrading.weekly.plist` and replace `__REPO_PATH__` with the full
   path to this checkout (`pwd` prints it).
2. Install it:

   ```bash
   cp scripts/scheduling/com.peergrading.weekly.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.peergrading.weekly.plist
   ```

3. Check it is registered:

   ```bash
   launchctl list | grep peergrading
   ```

Runs Sundays at 09:00. Output goes to `/tmp/peergrading.log`. To stop:

```bash
launchctl unload ~/Library/LaunchAgents/com.peergrading.weekly.plist
```

## Windows (Task Scheduler)

Run once in PowerShell, from this checkout, replacing the path if you moved it:

```powershell
$repo = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$repo\run.ps1`"" `
    -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 9am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName "PeerGrading Weekly" `
    -Action $action -Trigger $trigger -Settings $settings -Description `
    "Weekly peer-grading computation against the synced SharePoint folder."
```

`-StartWhenAvailable` means a missed run (laptop asleep) is picked up later.

To check or remove:

```powershell
Get-ScheduledTask -TaskName "PeerGrading Weekly"
Unregister-ScheduledTask -TaskName "PeerGrading Weekly"
```
