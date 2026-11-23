# Keeps the Jarvis Brain running: started by the 'Jarvis Brain' task at logon (see register_task.ps1).
# At logon Tailscale may not have its address yet, so binding fails and the Brain exits; this loop
# simply starts it again 30 seconds later. Output goes to data\brain.log, cut when it passes 1 MB.
$repo = Split-Path -Parent $PSScriptRoot
$log = Join-Path $repo 'data\brain.log'
New-Item -ItemType Directory -Force (Join-Path $repo 'data') | Out-Null
Set-Location $repo

while ($true) {
    if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Remove-Item $log }
    Add-Content -Encoding utf8 $log "==== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') starting ===="
    cmd.exe /c "uv run --no-sync jarvis-brain >> `"$log`" 2>&1"
    Add-Content -Encoding utf8 $log "==== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') exited with $LASTEXITCODE, retrying in 30 s ===="
    Start-Sleep -Seconds 30
}
