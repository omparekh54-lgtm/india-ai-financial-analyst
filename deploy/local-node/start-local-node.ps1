# Run the local research node without Docker (Windows PowerShell). Needs Python 3.11+.
#   .\start-local-node.ps1 check    # NSE reachability check
#   .\start-local-node.ps1          # worker + daily refresh
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Api = Resolve-Path (Join-Path $Here "..\..\apps\api")
$EnvFile = Join-Path $Here ".env"
if (-not (Test-Path $EnvFile)) { throw "Create $EnvFile from .env.example first." }
$Venv = Join-Path $Here ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $Python)) {
  py -3.11 -m venv $Venv
  & $Python -m pip install --upgrade pip
  & $Python -m pip install -e "$Api[market_imports]"
}
Get-Content $EnvFile | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
    [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
  }
}
Set-Location $Api
if ($args.Count -gt 0 -and $args[0] -eq "check") {
  & $Python scripts/check_nse_reachability.py
  exit $LASTEXITCODE
}
& $Python scripts/run_local_node.py @args
