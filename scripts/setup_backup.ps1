# Sets up the backup Brain on the laptop: the Jarvis that answers while the desktop is off.
# Run it on the laptop, from the jarvis checkout, with job-finder cloned beside it (../job-finder):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_backup.ps1 -Desktop <ssh host> -VaultDir <vault folder>
# It copies JARVIS_TOKEN and CLAUDE_CODE_OAUTH_TOKEN from the desktop's .env over SSH (never printing them),
# writes this checkout's .env for the backup role, installs the environment, registers the 'Jarvis Brain'
# logon task, and puts a Jarvis shortcut on the desktop that opens http://127.0.0.1:<port>/ as an app.
param(
    [Parameter(Mandatory = $true)][string]$Desktop,       # the SSH host of the desktop, as in `ssh <host>`
    [Parameter(Mandatory = $true)][string]$VaultDir,      # this laptop's copy of the vault (holds CLAUDE_INDEX.md)
    [string]$PrimaryUrl = '',                             # the desktop Brain; default: http://<its Tailscale IP>:8700
    [string]$DesktopEnv = 'PycharmProjects\jarvis\.env',  # the desktop's .env, relative to its home folder
    [int]$Port = 8700,
    [switch]$Force                                        # replace an existing .env
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repo '.env'

foreach ($tool in 'uv', 'claude', 'ssh') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool is not installed on this laptop." }
}
if (-not (Test-Path (Join-Path (Split-Path -Parent $repo) 'job-finder\pyproject.toml'))) {
    throw 'Clone job-finder next to jarvis first (../job-finder).'
}
if (-not (Test-Path (Join-Path $VaultDir 'CLAUDE_INDEX.md'))) { throw "No CLAUDE_INDEX.md in $VaultDir." }
if ((Test-Path $envFile) -and -not $Force) { throw '.env exists already; run again with -Force to replace it.' }

# The two secrets come from the desktop, so both Brains accept the same token and use the same subscription.
$remote = ssh $Desktop "type $DesktopEnv"
if ($LASTEXITCODE -ne 0) { throw "Could not read $DesktopEnv on $Desktop over SSH." }
$values = @{}
foreach ($line in $remote) {
    if ($line -match '^\s*(JARVIS_TOKEN|CLAUDE_CODE_OAUTH_TOKEN|JARVIS_HOST|JARVIS_PORT)\s*=\s*(.*?)\s*$') {
        $values[$Matches[1]] = $Matches[2].Trim('"', "'")
    }
}
if (-not $values['JARVIS_TOKEN']) { throw "No JARVIS_TOKEN in the desktop's .env." }
if (-not $PrimaryUrl) {
    $desktopPort = if ($values['JARVIS_PORT']) { $values['JARVIS_PORT'] } else { '8700' }
    $PrimaryUrl = "http://$($values['JARVIS_HOST']):$desktopPort"
}

$lines = @(
    '# The backup Brain (written by scripts\setup_backup.ps1). Answers on this laptop while the desktop is off.',
    "JARVIS_TOKEN=$($values['JARVIS_TOKEN'])",
    'JARVIS_HOST=127.0.0.1',
    "JARVIS_PORT=$Port",
    'JARVIS_ROLE=backup',
    "JARVIS_PRIMARY_URL=$PrimaryUrl",
    "VAULT_DIR=$VaultDir",
    "ROADMAP_PATH=$(Join-Path $VaultDir '02_Projects\Jarvis\Jarvis - Roadmap.md')",
    "CLAUDE_CODE_OAUTH_TOKEN=$($values['CLAUDE_CODE_OAUTH_TOKEN'])"
)
[IO.File]::WriteAllLines($envFile, $lines)
Write-Output "Wrote .env for the backup (desktop: $PrimaryUrl)."

Push-Location $repo
try { uv sync; if ($LASTEXITCODE -ne 0) { throw 'uv sync failed.' } } finally { Pop-Location }

& (Join-Path $PSScriptRoot 'register_task.ps1')
Start-ScheduledTask -TaskName 'Jarvis Brain'

$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
          "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($edge) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Jarvis.lnk'))
    $link.TargetPath = $edge
    $link.Arguments = "--app=http://127.0.0.1:$Port/"
    $link.Save()
    Write-Output 'Jarvis shortcut added to the desktop.'
}
Write-Output "Done. Open Jarvis from the shortcut: it uses the desktop when it answers and this laptop when it does not."
