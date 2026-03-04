param(
    [ValidateSet("test", "live")]
    [string]$Target = "test",
    [string]$SshHost = "",
    [switch]$NoPi,
    [switch]$RestartServices,
    [switch]$DryRun,
    [switch]$AllowLive
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "mas004_deploy_targets.ps1")

$targetMeta = Get-Mas004TargetMeta -Target $Target
$resolvedSshHost = Resolve-Mas004SshHost -Target $Target -SshHost $SshHost

if ($Target -eq "live" -and -not $AllowLive) {
    throw "LIVE sync is blocked by policy. Re-run with -Target live -AllowLive for explicit release deployment."
}

$mainRepoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$gitRoot = Split-Path $mainRepoPath -Parent

$repos = @(
    @{ Name = "MAS-004_RPI-Databridge"; Local = Join-Path $gitRoot "MAS-004_RPI-Databridge"; Remote = "/opt/MAS-004_RPI-Databridge"; Service = "mas004-rpi-databridge.service"; Main = $true },
    @{ Name = "MAS-004_ESP32-PLC-Bridge"; Local = Join-Path $gitRoot "MAS-004_ESP32-PLC-Bridge"; Remote = "/opt/MAS-004_ESP32-PLC-Bridge"; Service = "mas004-esp32-plc-bridge.service"; Main = $false },
    @{ Name = "MAS-004_VJ3350-Ultimate-Bridge"; Local = Join-Path $gitRoot "MAS-004_VJ3350-Ultimate-Bridge"; Remote = "/opt/MAS-004_VJ3350-Ultimate-Bridge"; Service = "mas004-vj3350-ultimate-bridge.service"; Main = $false },
    @{ Name = "MAS-004_VJ6530-ZBC-Bridge"; Local = Join-Path $gitRoot "MAS-004_VJ6530-ZBC-Bridge"; Remote = "/opt/MAS-004_VJ6530-ZBC-Bridge"; Service = "mas004-vj6530-zbc-bridge.service"; Main = $false }
)

function Invoke-Step([string]$Message, [scriptblock]$Action) {
    Write-Host "==> $Message" -ForegroundColor Cyan
    if ($DryRun) {
        Write-Host "    [DRY-RUN] skipped" -ForegroundColor DarkYellow
        return
    }
    & $Action
}

function Test-RemoteReachable([string]$SshTarget) {
    if ($NoPi) {
        return $false
    }
    try {
        $probe = ssh -o ConnectTimeout=5 $SshTarget "echo MAS004_REMOTE_OK" 2>$null
        return ($probe -match "MAS004_REMOTE_OK")
    } catch {
        return $false
    }
}

function Sync-LocalRepo($repo) {
    if (-not (Test-Path $repo.Local)) {
        Write-Host "[LOCAL] $($repo.Name): missing path $($repo.Local)" -ForegroundColor Yellow
        return
    }

    Invoke-Step "[LOCAL] $($repo.Name): git fetch" {
        git -C $repo.Local fetch --all --prune | Out-Host
    }

    $dirty = [bool](git -C $repo.Local status --porcelain)
    if ($dirty) {
        Write-Host "[LOCAL] $($repo.Name): dirty -> skip pull" -ForegroundColor Yellow
        return
    }

    Invoke-Step "[LOCAL] $($repo.Name): git pull --ff-only" {
        git -C $repo.Local pull --ff-only | Out-Host
    }
}

function Sync-RemoteRepo($repo) {
    $checkScript = @'
if [ ! -d '__REMOTE_PATH__' ]; then
  echo REMOTE_MISSING
  exit 0
fi
cd '__REMOTE_PATH__' || exit 2
if git status --porcelain | grep -q .; then
  echo REMOTE_DIRTY
else
  echo REMOTE_CLEAN
fi
'@
    $checkScript = $checkScript.Replace("__REMOTE_PATH__", $repo.Remote)
    $state = ""
    try {
        $state = (ssh -o ConnectTimeout=5 $resolvedSshHost $checkScript 2>$null | Select-Object -First 1).Trim()
    } catch {
        $state = "REMOTE_UNREACHABLE"
    }

    if ($state -eq "REMOTE_UNREACHABLE" -or -not $state) {
        Write-Host "[PI/$Target] $($repo.Name): unreachable host $resolvedSshHost -> skip" -ForegroundColor Yellow
        return
    }
    if ($state -eq "REMOTE_MISSING") {
        Write-Host "[PI/$Target] $($repo.Name): missing path $($repo.Remote)" -ForegroundColor Yellow
        return
    }
    if ($state -eq "REMOTE_DIRTY") {
        Write-Host "[PI/$Target] $($repo.Name): dirty -> skip pull (manual decision required)" -ForegroundColor Yellow
        return
    }

    Invoke-Step "[PI/$Target] $($repo.Name): git pull --ff-only" {
        ssh $resolvedSshHost "cd '$($repo.Remote)' && git pull --ff-only" | Out-Host
    }

    if ($RestartServices) {
        Invoke-Step "[PI/$Target] $($repo.Name): restart $($repo.Service)" {
            ssh $resolvedSshHost "sudo systemctl restart $($repo.Service) && systemctl is-active $($repo.Service)" | Out-Host
        }
    }
}

Write-Host ("Target profile: {0} ({1}) -> {2}" -f $targetMeta.name, $targetMeta.role, $resolvedSshHost) -ForegroundColor Green
if ($Target -eq "live") {
    Write-Host "LIVE deployment mode enabled by explicit -AllowLive." -ForegroundColor Yellow
}

foreach ($repo in $repos) {
    Sync-LocalRepo -repo $repo
}

$remoteReachable = $false
if (-not $NoPi) {
    $remoteReachable = Test-RemoteReachable -SshTarget $resolvedSshHost
    if (-not $remoteReachable) {
        Write-Host "[PI/$Target] host unreachable: $resolvedSshHost (local sync completed, remote skipped)" -ForegroundColor Yellow
    }
}

if (-not $NoPi -and $remoteReachable) {
    foreach ($repo in $repos) {
        Sync-RemoteRepo -repo $repo
    }
}

Write-Host "==> Final status" -ForegroundColor Green
& (Join-Path $PSScriptRoot "mas004_multirepo_status.ps1") -Target $Target -SshHost $resolvedSshHost
