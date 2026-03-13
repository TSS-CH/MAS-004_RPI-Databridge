param(
    [ValidateSet("test", "live")]
    [string]$Target = "test",
    [string]$SshHost = "",
    [switch]$AsJson
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "mas004_deploy_targets.ps1")

$targetMeta = Get-Mas004TargetMeta -Target $Target
$resolvedSshHost = Resolve-Mas004SshHost -Target $Target -SshHost $SshHost

$mainRepoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$gitRoot = Split-Path $mainRepoPath -Parent

$repos = @(
    @{ Name = "MAS-004_RPI-Databridge"; Local = Join-Path $gitRoot "MAS-004_RPI-Databridge"; Remote = "/opt/MAS-004_RPI-Databridge"; Service = "mas004-rpi-databridge.service"; Main = $true },
    @{ Name = "MAS-004_ESP32-PLC-Bridge"; Local = Join-Path $gitRoot "MAS-004_ESP32-PLC-Bridge"; Remote = "/opt/MAS-004_ESP32-PLC-Bridge"; Service = "mas004-esp32-plc-bridge.service"; Main = $false },
    @{ Name = "MAS-004_ESP32-PLC-Firmware"; Local = Join-Path $gitRoot "MAS-004_ESP32-PLC-Firmware"; Remote = "/opt/MAS-004_ESP32-PLC-Firmware"; Service = ""; Main = $false },
    @{ Name = "MAS-004_VJ3350-Ultimate-Bridge"; Local = Join-Path $gitRoot "MAS-004_VJ3350-Ultimate-Bridge"; Remote = "/opt/MAS-004_VJ3350-Ultimate-Bridge"; Service = "mas004-vj3350-ultimate-bridge.service"; Main = $false },
    @{ Name = "MAS-004_VJ6530-ZBC-Bridge"; Local = Join-Path $gitRoot "MAS-004_VJ6530-ZBC-Bridge"; Remote = "/opt/MAS-004_VJ6530-ZBC-Bridge"; Service = "mas004-vj6530-zbc-bridge.service"; Main = $false },
    @{ Name = "MAS-004_ZBC-Library"; Local = Join-Path $gitRoot "MAS-004_ZBC-Library"; Remote = "/opt/MAS-004_ZBC-Library"; Service = ""; Main = $false }
)

function Get-MapValue([hashtable]$Map, [string]$Key, $Default) {
    if ($Map.ContainsKey($Key) -and $null -ne $Map[$Key] -and $Map[$Key] -ne "") {
        return $Map[$Key]
    }
    return $Default
}

function Get-LocalState([string]$Path) {
    if (-not (Test-Path $Path)) {
        return @{ exists = $false; head = ""; branch = ""; status = "MISSING"; dirty = $false }
    }

    $head = (git -C $Path rev-parse --short HEAD).Trim()
    $branch = (git -C $Path rev-parse --abbrev-ref HEAD).Trim()
    $statusSummary = (git -C $Path status -sb | Select-Object -First 1).Trim()
    $dirty = [bool](git -C $Path status --porcelain)

    return @{ exists = $true; head = $head; branch = $branch; status = $statusSummary; dirty = $dirty }
}

function Get-RemoteState([string]$SshTarget, [string]$RemotePath, [string]$Service) {
    $script = @'
if [ -d '__REMOTE_PATH__' ]; then
  cd '__REMOTE_PATH__' || exit 2
  echo EXISTS=yes
  echo HEAD=$(git rev-parse --short HEAD)
  echo BRANCH=$(git rev-parse --abbrev-ref HEAD)
  echo STATUS=$(git status -sb | head -n 1)
  if git status --porcelain | grep -q .; then
    echo DIRTY=yes
  else
    echo DIRTY=no
  fi
else
  echo EXISTS=no
fi
if [ -n '__SERVICE__' ]; then
  if systemctl list-unit-files '__SERVICE__' >/dev/null 2>&1; then
    echo SERVICE=$(systemctl is-active '__SERVICE__' 2>/dev/null)
  else
    echo SERVICE=missing
  fi
else
  echo SERVICE=n/a
fi
'@

    $script = $script.Replace("__REMOTE_PATH__", $RemotePath).Replace("__SERVICE__", $Service)

    try {
        $lines = ssh -o ConnectTimeout=5 $SshTarget $script 2>$null
    } catch {
        return @{
            reachable = $false
            exists = $false
            head = ""
            branch = ""
            status = "UNREACHABLE"
            dirty = $false
            service = "unreachable"
            error = $_.Exception.Message
        }
    }

    if (-not $lines) {
        return @{
            reachable = $false
            exists = $false
            head = ""
            branch = ""
            status = "UNREACHABLE"
            dirty = $false
            service = "unreachable"
            error = "SSH returned no output"
        }
    }

    $map = @{}
    foreach ($line in $lines) {
        if ($line -like "*=*") {
            $parts = $line -split "=", 2
            if ($parts.Count -eq 2) {
                $map[$parts[0].Trim()] = $parts[1].Trim()
            }
        }
    }

    $exists = (Get-MapValue $map "EXISTS" "no") -eq "yes"
    $dirty = (Get-MapValue $map "DIRTY" "no") -eq "yes"
    return @{
        reachable = $true
        exists = $exists
        head = (Get-MapValue $map "HEAD" "")
        branch = (Get-MapValue $map "BRANCH" "")
        status = (Get-MapValue $map "STATUS" "MISSING")
        dirty = $dirty
        service = (Get-MapValue $map "SERVICE" "unknown")
        error = ""
    }
}

$report = @()
foreach ($r in $repos) {
    $local = Get-LocalState -Path $r.Local
    $remote = Get-RemoteState -SshTarget $resolvedSshHost -RemotePath $r.Remote -Service $r.Service

    $inSync = $local.exists -and $remote.reachable -and $remote.exists -and ($local.head -eq $remote.head) -and (-not $local.dirty) -and (-not $remote.dirty)
    $report += [pscustomobject]@{
        target = $Target
        role = $targetMeta.role
        ssh_host = $resolvedSshHost
        name = $r.Name
        main = [bool]$r.Main
        local_head = $local.head
        local_status = $local.status
        local_dirty = [bool]$local.dirty
        remote_reachable = [bool]$remote.reachable
        remote_head = $remote.head
        remote_status = $remote.status
        remote_dirty = [bool]$remote.dirty
        service = $remote.service
        in_sync = [bool]$inSync
        remote_error = $remote.error
    }
}

if ($AsJson) {
    $report | ConvertTo-Json -Depth 6
} else {
    Write-Host ("Target profile: {0} ({1}) -> {2}" -f $targetMeta.name, $targetMeta.role, $resolvedSshHost) -ForegroundColor Cyan
    $report | Format-Table -AutoSize target, name, main, local_head, local_dirty, remote_reachable, remote_head, remote_dirty, service, in_sync
}
