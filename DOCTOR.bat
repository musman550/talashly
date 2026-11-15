@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [X] Pehle INSTALL.bat chalayein.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m engine doctor
echo.
pause
