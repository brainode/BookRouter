$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$PipExe = Join-Path $VenvDir "Scripts\pip.exe"

python -m venv $VenvDir
& $PythonExe -m pip install --upgrade pip
& $PipExe install -r (Join-Path $ProjectDir "requirements.txt")

$EnvFile = Join-Path $ProjectDir ".env"
$EnvExampleFile = Join-Path $ProjectDir ".env.example"
if (-not (Test-Path $EnvFile)) {
    Copy-Item $EnvExampleFile $EnvFile
}

Write-Host "Virtual environment created at $VenvDir"
Write-Host "Activate it with: .\.venv\Scripts\Activate.ps1"
