# ============================================================
#  Construye OBS-AutoConfig.exe (un solo archivo, autocontenido).
#  El .exe resultante corre en Windows 10/11 SIN instalar Python.
#  Uso:  pwsh -ExecutionPolicy Bypass -File build.ps1
#        (o)  click derecho > "Ejecutar con PowerShell"
# ============================================================
$ErrorActionPreference = 'Stop'

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Construyendo OBS-AutoConfig.exe" -ForegroundColor Cyan
Write-Host "============================================================`n" -ForegroundColor Cyan

# 1) Verificar Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: No se encontro Python. Instalalo desde python.org y marca 'Add to PATH'." -ForegroundColor Red
    exit 1
}
python --version

# 2) Instalar PyInstaller
Write-Host "`nInstalando dependencias de construccion..." -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 3) Construir
Write-Host "`nGenerando el ejecutable (puede tardar 1-2 minutos)..." -ForegroundColor Cyan
python -m PyInstaller --onefile --windowed --name OBS-AutoConfig --clean --noconfirm --hidden-import optimizar escanear.py

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "  LISTO!  El ejecutable esta en: dist\OBS-AutoConfig.exe" -ForegroundColor Green
Write-Host "  Copialo al otro PC (Windows 10) y ejecutalo. No necesita Python." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
