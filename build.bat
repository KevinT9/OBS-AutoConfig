@echo off
REM ============================================================
REM  Construye OBS-AutoConfig.exe (un solo archivo, autocontenido).
REM  El .exe resultante corre en Windows 10/11 SIN instalar Python.
REM  Uso: doble clic en este archivo, o ejecutar desde una consola.
REM ============================================================
setlocal

echo ============================================================
echo   Construyendo OBS-AutoConfig.exe
echo ============================================================
echo.

REM 1) Verificar que Python esta disponible
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: No se encontro Python en este PC.
    echo Instala Python desde https://www.python.org/downloads/ y reintenta.
    echo IMPORTANTE: durante la instalacion marca "Add python.exe to PATH".
    pause
    exit /b 1
)

REM 2) Instalar PyInstaller
echo Instalando dependencias de construccion...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: No se pudo instalar PyInstaller.
    pause
    exit /b 1
)

REM 3) Construir el ejecutable
echo.
echo Generando el ejecutable (esto puede tardar 1-2 minutos)...
python -m PyInstaller --onefile --windowed --name OBS-AutoConfig --clean --noconfirm --hidden-import interfaz --hidden-import optimizar escanear.py
if errorlevel 1 (
    echo.
    echo ERROR durante la construccion.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   LISTO!  El ejecutable esta en:
echo       dist\OBS-AutoConfig.exe
echo.
echo   Copia ESE archivo al otro PC (Windows 10) y ejecutalo.
echo   No necesita Python ni ninguna otra instalacion.
echo ============================================================
pause
