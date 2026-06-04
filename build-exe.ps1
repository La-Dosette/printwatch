$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Venv = Join-Path $Root ".venv-build"
$Python = Join-Path $Venv "Scripts\python.exe"

function Write-Step($Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

Write-Host "Build PrintWatchAgent.exe" -ForegroundColor White
Write-Host "Dossier: $Root" -ForegroundColor DarkGray

if (-not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python est introuvable. Installe Python 3.11+ puis relance ce script."
}

if (-not (Test-Path $Python)) {
    Write-Step "Creation de l'environnement de build"
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3 -m venv $Venv
    } else {
        python -m venv $Venv
    }
}

Write-Step "Installation des dependances de build"
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $Root "requirements.txt")
& $Python -m pip install pyinstaller

Write-Step "Generation de l'executable sans console"
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "PrintWatchAgent" `
    --add-data "docs;docs" `
    "printwatch_agent.py"

Write-Host ""
Write-Host "Exe cree :" -ForegroundColor Green
Write-Host (Join-Path $Root "dist\PrintWatchAgent.exe") -ForegroundColor White
