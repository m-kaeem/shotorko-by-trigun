# Run on the OFFLINE computer, inside the project folder that contains .\wheels (made by make_offline_bundle.ps1).
# Creates .venv and installs everything from .\wheels only: pip never touches the internet (--no-index).
#   powershell -ExecutionPolicy Bypass -File scripts\install_offline.ps1
# Then start the app with:  .venv\Scripts\python.exe run.py
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path "wheels")) { throw "No .\wheels folder. Run scripts\make_offline_bundle.ps1 on a computer with internet first." }
function Find-Python {
  foreach ($c in @("python", "py")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike "*WindowsApps*") { return $cmd.Source }
  }
  $found = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "C:\Program Files\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
  if ($found) { return $found.FullName }
  throw "Python 3.10-3.13 not found. Install it first (python.org or winget install Python.Python.3.12)."
}
if (-not (Test-Path ".venv\Scripts\python.exe")) { & (Find-Python) -m venv .venv }
.venv\Scripts\python.exe -m pip install --no-index --find-links wheels --upgrade pip
.venv\Scripts\python.exe -m pip install --no-index --find-links wheels -r requirements.txt
.venv\Scripts\python.exe -c "import lightgbm, fastapi, pandas; print('Shotorko dependencies OK (installed offline)')"
Write-Host "Start the app:  .venv\Scripts\python.exe run.py   (add `$env:SHOTORKO_OFFLINE='1' to forbid any internet use)"
