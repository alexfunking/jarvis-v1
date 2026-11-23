# Creates (or replaces) the Task Scheduler task that keeps the Jarvis Brain running. Run it from any folder:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_task.ps1
# The task starts scripts\brain.ps1 in a hidden window at logon, for the current user only (no admin rights),
# and has no time limit. A second start while it runs is ignored.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$user = "$env:USERDOMAIN\$env:USERNAME"

$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$logon.Delay = 'PT30S'
$action = New-ScheduledTaskAction -Execute powershell.exe -WorkingDirectory $repo `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$repo\scripts\brain.ps1`""

Register-ScheduledTask -TaskName 'Jarvis Brain' -Force -Action $action -Trigger $logon `
    -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)) |
    Out-Null
Write-Output "Task 'Jarvis Brain' registered for $user."
