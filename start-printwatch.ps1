$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$UiUrl = "https://la-dosette.github.io/printwatch/"
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Marker = Join-Path $Venv ".printwatch-deps-ok"

function Write-Step($Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

Write-Host "PrintWatch Agent" -ForegroundColor White
Write-Host "Dossier: $Root" -ForegroundColor DarkGray

if (-not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "Python est introuvable. Installe Python 3.11+ puis relance ce fichier." -ForegroundColor Red
    Read-Host "Appuie sur Entree pour fermer"
    exit 1
}

if (-not (Test-Path $Python)) {
    Write-Step "Creation de l'environnement Python local (.venv)"
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3 -m venv $Venv
    } else {
        python -m venv $Venv
    }
}

if (-not (Test-Path $Marker)) {
    Write-Step "Installation des dependances"
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -r (Join-Path $Root "requirements.txt")
    New-Item -ItemType File -Path $Marker -Force | Out-Null
}

Write-Step "Ouverture de l'interface"
Start-Process $UiUrl

Write-Step "Demarrage de l'agent local"
Write-Host "Interface : $UiUrl" -ForegroundColor DarkGray
Write-Host "Agent     : http://localhost:8088" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Garde cette fenetre ouverte tant que tu utilises PrintWatch." -ForegroundColor Yellow
Write-Host "Pour arreter l'agent : Ctrl+C puis O/N selon Windows." -ForegroundColor Yellow
Write-Host ""

& $Python (Join-Path $Root "app.py")

Write-Host ""
Read-Host "Agent arrete. Appuie sur Entree pour fermer"
