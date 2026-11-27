# Run ONCE on a computer WITH internet (same Windows + Python version as the target, 3.10-3.13).
# Downloads every Python package Shotorko needs into .\wheels so the app can later be installed with no internet.
#   powershell -ExecutionPolicy Bypass -File scripts\make_offline_bundle.ps1
# Then copy the whole project folder (including wheels\) to the offline computer, e.g. on a USB stick.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
function Find-Python {
  foreach ($c in @("python", "py")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike "*WindowsApps*") { return $cmd.Source }
  }
  $found = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "C:\Program Files\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
  if ($found) { return $found.FullName }
  throw "Python 3.10-3.13 not found. Install it first (python.org or winget install Python.Python.3.12)."
}
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { Find-Python }
& $py --version
& $py -m pip download -r requirements.txt -d wheels
& $py -m pip download pip setuptools wheel -d wheels
$n = (Get-ChildItem wheels -File).Count
$mb = [math]::Round(((Get-ChildItem wheels -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "Done: $n files ($mb MB) in .\wheels. Copy the project folder, wheels included, to the offline computer."
