[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$')]
    [string]$Organization = 'blackoutsecure',

    [ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')]
    [string]$Repository = '',

    [ValidatePattern('^$|^[0-9]+$')]
    [string]$RunId = '',

    [ValidateRange(1, 65535)]
    [int]$Port = 8765,

    [ValidateSet('gatekeeper', 'gatewall')]
    [string]$Profile = 'gatekeeper',

    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

if ([bool]$Repository -ne [bool]$RunId) {
    throw 'Repository and RunId must be supplied together.'
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'GitHub CLI (gh) is required. Install it, then run gh auth login.'
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $python) {
    throw 'Python 3 is required and was not found on PATH.'
}

& gh auth status
if ($LASTEXITCODE -ne 0) {
    throw 'GitHub CLI is not authenticated. Run gh auth login first.'
}

$server = Join-Path $PSScriptRoot '..\tools\gatekeeper-app-setup\server.py'
$arguments = @(
    $server,
    '--organization', $Organization,
    '--port', [string]$Port,
    '--profile', $Profile
)
if ($Repository) {
    $arguments += @('--repository', $Repository, '--run-id', $RunId)
}
if ($NoBrowser) {
    $arguments += '--no-browser'
}

Write-Host 'Starting the loopback-only Gatekeeper App setup helper.' -ForegroundColor Cyan
Write-Host 'Press Ctrl+C after the browser reports that setup is verified.' -ForegroundColor DarkGray
& $python.Source @arguments
exit $LASTEXITCODE
