<#
.SYNOPSIS
  install-gost-windows.ps1 — run a gost v3 SOCKS5 server on a Windows PC
  that sits on the domestic network, bound only to its Tailscale IP.

.DESCRIPTION
  Backup option for the box-side proxychains4 setup. A domestic Windows PC
  runs gost v3 (official go-gost releases only) as a SOCKS5 server with
  username/password auth, listening ONLY on its Tailscale (100.64.0.0/10)
  IPv4. The box reaches this SOCKS5 server over Tailscale.

.PARAMETER ListenIP
  IP to bind. Default: auto-detect the 100.x IPv4 of the Tailscale adapter.

.PARAMETER Port
  Listening port. Default 1080.

.PARAMETER User
  SOCKS5 username. Default 'wechat'.

.PARAMETER Pass
  SOCKS5 password. If empty, a random 24-char password is generated.

.PARAMETER InstallDir
  Install directory. Default C:\gost.

.PARAMETER Check
  Read-only checks (process running, port listening, firewall rule, exit country).
  Does not require Administrator, but some checks may be limited without it.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install-gost-windows.ps1
  powershell -ExecutionPolicy Bypass -File install-gost-windows.ps1 -Check
#>

param(
  [string]$ListenIP = "",
  [int]$Port = 1080,
  [string]$User = "",
  [string]$Pass = "",
  [string]$InstallDir = "C:\gost",
  [switch]$Check
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
  [Net.ServicePointManager]::SecurityProtocol =
    [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

$TaskName = 'gost-wx-socks5'
$FirewallRuleName = 'gost-wx-socks5-tailscale'
$YamlPath = Join-Path $InstallDir 'gost.yaml'
$GostExe = Join-Path $InstallDir 'gost.exe'

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
function Test-IsAdmin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p = New-Object Security.Principal.WindowsPrincipal($id)
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Find-Tailscale {
  $c = Get-Command tailscale.exe -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  $paths = @(
    "$env:ProgramFiles\Tailscale\tailscale.exe",
    "${env:ProgramFiles(x86)}\Tailscale\tailscale.exe",
    "$env:LOCALAPPDATA\Tailscale\tailscale.exe"
  )
  foreach ($p in $paths) { if (Test-Path -LiteralPath $p) { return $p } }
  return $null
}

function Detect-TailscaleIP {
  $adapter = Get-NetAdapter -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceDescription -match 'Tailscale' -or $_.Name -match 'Tailscale' } |
    Select-Object -First 1
  if ($adapter) {
    $ip = Get-NetIPAddress -InterfaceIndex $adapter.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
      Where-Object { $_.IPAddress.StartsWith('100.') } |
      Select-Object -First 1 -ExpandProperty IPAddress
    if ($ip) { return $ip }
  }
  $ip2 = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress.StartsWith('100.') } |
    Select-Object -First 1 -ExpandProperty IPAddress
  return $ip2
}

function New-RandomPassword {
  $chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
  $rng = [Security.Cryptography.RNGCryptoServiceProvider]::new()
  $sb = New-Object System.Text.StringBuilder
  $byte = New-Object byte[] 1
  while ($sb.Length -lt 24) {
    $rng.GetBytes($byte)
    [void]$sb.Append($chars[$byte[0] % $chars.Length])
  }
  return $sb.ToString()
}

function ConvertTo-YamlString {
  param([string]$S)
  return "'" + ($S -replace "'", "''") + "'"
}

function Get-ExitInfo {
  $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
  if ($curl) {
    $json = & curl.exe -s -m 20 https://ipinfo.io/json 2>$null
    if ($json) {
      try { return (($json -join "`n") | ConvertFrom-Json) } catch { }
    }
  }
  try { return Invoke-RestMethod -Uri 'https://ipinfo.io/json' -TimeoutSec 20 } catch { }
  return $null
}

function Invoke-Check {
  $fail = $false

  if (-not $ListenIP) { $ListenIP = Detect-TailscaleIP }
  if ($ListenIP) {
    Write-Host "INFO ListenIP: $ListenIP"
  } else {
    Write-Host "FAIL could not detect a 100.x Tailscale IPv4 (pass -ListenIP)"
    $fail = $true
  }

  $p = Get-Process -Name 'gost' -ErrorAction SilentlyContinue
  if ($p) {
    Write-Host "PASS gost process running (PID $($p.Id -join ','))"
  } else {
    Write-Host "FAIL gost process not running"
    $fail = $true
  }

  try {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
    if ($conns) {
      $addrs = ($conns.LocalAddress | Sort-Object -Unique) -join ','
      Write-Host "PASS port $Port listening on: $addrs"
    } else {
      Write-Host "FAIL port $Port not listening"
      $fail = $true
    }
  } catch {
    Write-Host "WARN could not query port listener (may need Administrator)"
  }

  try {
    $rule = Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction Stop
    if ($rule) {
      Write-Host "PASS firewall rule '$FirewallRuleName' exists (enabled=$($rule.Enabled))"
    } else {
      Write-Host "FAIL firewall rule '$FirewallRuleName' missing"
      $fail = $true
    }
  } catch {
    Write-Host "WARN could not query firewall rule (may need Administrator)"
  }

  $info = Get-ExitInfo
  if ($info) {
    Write-Host "INFO exit country: $($info.country) org: $($info.org) ip: $($info.ip)"
    if ($info.country -ne 'CN') {
      Write-Host "FAIL exit country is $($info.country), not CN"
      $fail = $true
    } else {
      Write-Host "PASS exit country is CN"
    }
  } else {
    Write-Host "WARN could not query https://ipinfo.io/json"
  }

  if ($fail) { Write-Host "RESULT: FAIL"; exit 1 }
  Write-Host "RESULT: PASS"
  exit 0
}

if ($Check) { Invoke-Check }

# ---------------------------------------------------------------------------
# install mode
# ---------------------------------------------------------------------------
if (-not (Test-IsAdmin)) {
  Write-Error "This script must be run as Administrator (for firewall, ACL, scheduled task and powercfg)."
  exit 1
}

# 1. Tailscale must be installed and running.
$Tailscale = Find-Tailscale
if (-not $Tailscale) {
  Write-Host "Tailscale is not installed."
  Write-Host "Download and install it from: https://tailscale.com/download/windows"
  Write-Host "Then run: tailscale up  (and tailscale ip -4 must print a 100.x address)"
  exit 1
}
$TsIps = & $Tailscale ip -4 2>$null
if (-not $TsIps) {
  Write-Host "Tailscale is installed but not running / not logged in."
  Write-Host "Start Tailscale and log in, then rerun this script."
  Write-Host "Download: https://tailscale.com/download/windows"
  exit 1
}
Write-Host "Tailscale 100.x addresses:"
Write-Host $TsIps

# 2. Detect listen IP.
if (-not $ListenIP) {
  $ListenIP = Detect-TailscaleIP
}
if (-not $ListenIP) {
  Write-Error "Could not auto-detect a 100.x Tailscale IPv4. Pass -ListenIP explicitly."
  exit 1
}

# 3. User / password.
if (-not $User) { $User = 'wechat' }
$autoPass = $false
if (-not $Pass) {
  $Pass = New-RandomPassword
  $autoPass = $true
}

# 4. Download the latest official go-gost release (windows_amd64 .zip).
$release = Invoke-RestMethod -Uri 'https://api.github.com/repos/go-gost/gost/releases/latest' `
  -Headers @{ 'User-Agent' = 'gost-wx-installer' }

$asset = $release.assets |
  Where-Object { $_.name -match 'windows_amd64' -and $_.name -like '*.zip' -and $_.name -notmatch 'checksums' } |
  Select-Object -First 1
if (-not $asset) {
  Write-Error "No windows_amd64 .zip asset found in go-gost/gost latest release."
  exit 1
}
$zipName = $asset.name
$zipUrl = $asset.browser_download_url
Write-Host "Downloading $zipName ..."
$zipPath = Join-Path $env:TEMP $zipName
Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath

# Optional sha256 verification via checksums file.
$checksums = $release.assets |
  Where-Object { $_.name -match 'checksums' -and $_.name -match '\.txt$' } |
  Select-Object -First 1
if ($checksums) {
  $csPath = Join-Path $env:TEMP $checksums.name
  Invoke-WebRequest -Uri $checksums.browser_download_url -OutFile $csPath
  $line = Select-String -Path $csPath -Pattern ([regex]::Escape($zipName)) | Select-Object -First 1
  if ($line) {
    $expected = ($line.Line -split '\s+')[0]
    $actual = (Get-FileHash -Algorithm SHA256 -Path $zipPath).Hash.ToLowerInvariant()
    if ($actual -ne $expected.ToLowerInvariant()) {
      Write-Error "SHA256 mismatch for $zipName (expected $expected, got $actual). Aborting."
      exit 1
    }
    Write-Host "sha256 verified for $zipName"
  } else {
    Write-Warning "checksums file present but no entry for $zipName; skipping verification."
  }
}

# 5. Extract gost.exe.
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Expand-Archive -Path $zipPath -DestinationPath $InstallDir -Force
$found = Get-ChildItem -Path $InstallDir -Recurse -Filter 'gost.exe' | Select-Object -First 1
if (-not $found) {
  Write-Error "gost.exe not found after extraction under $InstallDir."
  exit 1
}
if ($found.FullName -ne $GostExe) {
  Copy-Item -LiteralPath $found.FullName -Destination $GostExe -Force
}
Write-Host "Installed gost.exe at $GostExe"

# 6. Write gost.yaml.
$yaml = @"
# gost v3 SOCKS5 service, bound ONLY to the Tailscale IP so it is not
# reachable from the LAN's non-Tailscale interfaces.
services:
- name: wx-socks5
  addr: "$($ListenIP):$Port"
  handler:
    type: socks5
    auth:
      username: $(ConvertTo-YamlString $User)
      password: $(ConvertTo-YamlString $Pass)
  listener:
    type: tcp
"@
[System.IO.File]::WriteAllText($YamlPath, $yaml, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Wrote $YamlPath"

# 7. ACL the yaml to Administrators + SYSTEM only (it contains the password).
# SIDs = Administrators, SYSTEM (locale-independent, works on Chinese Windows)
& icacls $YamlPath /inheritance:r /grant:r '*S-1-5-32-544:(F)' '*S-1-5-18:(F)' | Out-Null

# 8. Firewall rule: inbound TCP on the port, only from the Tailscale range.
Remove-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $FirewallRuleName `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
  -RemoteAddress '100.64.0.0/10' | Out-Null
Write-Host "Firewall rule '$FirewallRuleName' created (inbound TCP $Port from 100.64.0.0/10 only)"

# 9. Scheduled task: gost.exe -C gost.yaml as SYSTEM at startup, restart on failure.
$action = New-ScheduledTaskAction -Execute $GostExe -Argument ('-C "{0}"' -f $YamlPath)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$trigger = New-ScheduledTaskTrigger -AtStartup
# gost exits if the Tailscale IP is not up yet at boot; the restart settings retry every minute.
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Scheduled task '$TaskName' registered (SYSTEM, at startup, restart on failure)"

# 10. Keep the machine awake on AC power.
& powercfg /change standby-timeout-ac 0 2>$null
& powercfg /change hibernate-timeout-ac 0 2>$null
Write-Host "Disabled AC standby/hibernate timeouts (best effort)"

# 11. Start it now.
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 2
Write-Host "Started '$TaskName'"

# 12. IMPORTANT exit check: the PC's own gateway must NOT tunnel abroad.
$info = Get-ExitInfo
if ($info) {
  Write-Host ("Exit check: ip={0} country={1} org={2}" -f $info.ip, $info.country, $info.org)
  if ($info.country -ne 'CN') {
    Write-Host ""
    Write-Warning "IMPORTANT: this PC exits to country '$($info.country)', NOT CN!"
    Write-Host "This means the PC's gateway / side router is tunneling traffic abroad."
    Write-Host "Use a device on the domestic gateway, or add a GEOIP-CN direct rule on the side router."
    Write-Host "Do NOT change the gateway of a PC that needs the foreign route."
  } else {
    Write-Host "Exit check OK: country is CN."
  }
} else {
  Write-Warning "Could not query https://ipinfo.io/json to verify the exit country."
}

# 13. Summary.
Write-Host ""
Write-Host "--------------------------------------------------------------"
Write-Host "gost SOCKS5 server installed:"
Write-Host "  ListenIP : $ListenIP"
Write-Host "  Port     : $Port"
Write-Host "  User     : $User"
Write-Host "  Password : written to $YamlPath (Administrators+SYSTEM only)"
if ($autoPass) {
  Write-Host ""
  Write-Host "Auto-generated password (shown once):"
  Write-Host "  $Pass"
  Write-Host "Store it in the box secrets file (chmod 600) so install.sh can use it."
  Write-Host "PHONE_SOCKS_HOST=$ListenIP"
  Write-Host "PHONE_SOCKS_PORT=$Port"
  Write-Host "PHONE_SOCKS_USER=$User"
  Write-Host "PHONE_SOCKS_PASS=$Pass"
}
