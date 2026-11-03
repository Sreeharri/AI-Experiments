<#
.SYNOPSIS
    Start the exact PostgreSQL cluster used by Legal Radar.

.DESCRIPTION
    Idempotent and safe for a user-owned local cluster. The script first checks
    whether PostgreSQL is already accepting connections. If a start is needed,
    it resolves a directory containing PG_VERSION and postgresql.conf before
    invoking pg_ctl. It never guesses that an arbitrary folder is a cluster and
    never starts a different Windows service behind the user's back.

.EXAMPLE
    .\scripts\start_postgres_windows.ps1
    .\scripts\start_postgres_windows.ps1 `
        -PgBin "C:\Program Files\PostgreSQL\18\bin" `
        -DataDirectory "$env:LOCALAPPDATA\LegalRadar\PostgreSQL18\data"
#>
[CmdletBinding()]
param(
    [Alias("PostgresBin")]
    [string]$PgBin = "C:\Program Files\PostgreSQL\18\bin",
    [string]$DataDirectory = "",
    [string]$PgHost = "localhost",
    [ValidateRange(1, 65535)][int]$Port = 5432,
    [string]$Database = "legal_radar_backend",
    [string]$Username = "legal_radar_app",
    [ValidateRange(5, 300)][int]$TimeoutSeconds = 60,
    [switch]$Restart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pgIsReady = Join-Path $PgBin "pg_isready.exe"
$pgCtl = Join-Path $PgBin "pg_ctl.exe"

foreach ($executable in @($pgIsReady, $pgCtl)) {
    if (-not (Test-Path $executable -PathType Leaf)) {
        throw "PostgreSQL executable not found: '$executable'. Pass -PgBin (or -PostgresBin) with the correct bin directory."
    }
}

function Test-ClusterDirectory {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    return (
        (Test-Path (Join-Path $Path "PG_VERSION") -PathType Leaf) -and
        (Test-Path (Join-Path $Path "postgresql.conf") -PathType Leaf)
    )
}

function Resolve-ClusterDirectory {
    $candidates = [System.Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($DataDirectory)) {
        $candidates.Add($DataDirectory)
        $candidates.Add((Join-Path $DataDirectory "data"))
    }
    if (-not [string]::IsNullOrWhiteSpace($env:PGDATA)) {
        $candidates.Add($env:PGDATA)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $localRoot = Join-Path $env:LOCALAPPDATA "LegalRadar\PostgreSQL18"
        $candidates.Add((Join-Path $localRoot "data"))
        $candidates.Add($localRoot)
    }
    $candidates.Add("C:\Program Files\PostgreSQL\18\data")

    $checked = [System.Collections.Generic.List[string]]::new()
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        $expanded = [Environment]::ExpandEnvironmentVariables($candidate)
        if ($checked.Contains($expanded)) { continue }
        $checked.Add($expanded)
        if (Test-ClusterDirectory -Path $expanded) {
            return (Resolve-Path $expanded).Path
        }
    }

    $message = "No PostgreSQL cluster directory was found. A valid directory contains PG_VERSION and postgresql.conf. Checked:`n  " + ($checked -join "`n  ")
    throw $message
}

function Test-PostgresReady {
    param([int]$QuietTimeout = 3)
    & $pgIsReady -h $PgHost -p $Port -d $Database -U $Username -t $QuietTimeout *> $null
    return ($LASTEXITCODE -eq 0)
}

function Wait-PostgresReady {
    param([int]$Seconds)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-PostgresReady) { return $true }
        $remaining = [Math]::Max(0, [int]($deadline - (Get-Date)).TotalSeconds)
        Write-Host "  waiting for readiness... ${remaining}s remaining"
        Start-Sleep -Seconds 2
    }
    return (Test-PostgresReady)
}

if (Test-PostgresReady) {
    if (-not $Restart) {
        Write-Host "PostgreSQL is already accepting connections on ${PgHost}:${Port}/${Database}." -ForegroundColor Green
        exit 0
    }
}

$resolvedDataDirectory = Resolve-ClusterDirectory
$clusterVersion = (Get-Content (Join-Path $resolvedDataDirectory "PG_VERSION") -Raw).Trim()
Write-Host "Cluster: $resolvedDataDirectory (PostgreSQL $clusterVersion)"

if ($Restart -and (Test-PostgresReady)) {
    Write-Host "Stopping the selected cluster..." -ForegroundColor Yellow
    & $pgCtl -D $resolvedDataDirectory -m fast -w -t $TimeoutSeconds stop
    if ($LASTEXITCODE -ne 0) {
        throw "Could not stop PostgreSQL cluster '$resolvedDataDirectory'."
    }
}

$logDirectory = Join-Path $projectRoot "logs\postgres"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logFile = Join-Path $logDirectory ("postgres-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
Write-Host "Log file: $logFile"
Write-Host "Starting PostgreSQL with pg_ctl..."

& $pgCtl -D $resolvedDataDirectory -l $logFile -o "-p $Port" -W start
$startExitCode = $LASTEXITCODE
if ($startExitCode -ne 0 -and -not (Test-PostgresReady)) {
    if (Test-Path $logFile -PathType Leaf) {
        Write-Host "Last 40 log lines:" -ForegroundColor Yellow
        Get-Content $logFile -Tail 40
    }
    throw "pg_ctl could not start cluster '$resolvedDataDirectory' (exit $startExitCode)."
}

if (Wait-PostgresReady -Seconds $TimeoutSeconds) {
    Write-Host "PostgreSQL is accepting connections on ${PgHost}:${Port}/${Database}." -ForegroundColor Green
    exit 0
}

if (Test-Path $logFile -PathType Leaf) {
    Write-Host "Last 40 log lines:" -ForegroundColor Yellow
    Get-Content $logFile -Tail 40
}
throw "PostgreSQL did not become ready on ${PgHost}:${Port} within $TimeoutSeconds seconds."
