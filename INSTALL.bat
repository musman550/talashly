@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
title Talashly - Install
echo ==============================================
echo   Talashly - pehli dafa install
echo ==============================================
echo.

set "PY="
py -3 -c "import sys;sys.exit(0 if sys.version_info[:2]>=(3,11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto havepy
python -c "import sys;sys.exit(0 if sys.version_info[:2]>=(3,11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"

:havepy
if not defined PY (
  echo [X] Python 3.11 ya naya nahi mila.
  echo     1. https://www.python.org/downloads/ se Python install karein
  echo     2. Installer mein "Add python.exe to PATH" ZAROOR tick karein
  echo     3. Phir INSTALL.bat dobara chalayein
  echo.
  pause
  exit /b 1
)
echo [OK] Python mila: %PY%

if exist ".venv\Scripts\python.exe" goto havevenv
echo Virtual environment bana raha hoon...
%PY% -m venv .venv
if errorlevel 1 (
  echo [X] venv nahi ban saka.
  pause
  exit /b 1
)

:havevenv
echo Packages install ho rahe hain (pehli dafa 1-3 minute lag sakte hain)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [X] Packages install nahi hue. Internet check karein aur dobara chalayein.
  pause
  exit /b 1
)

echo.
echo Ab system check:
".venv\Scripts\python.exe" -m engine doctor
echo.
echo ==============================================
echo   Install mukammal. START.bat chalayein.
echo ==============================================
pause
