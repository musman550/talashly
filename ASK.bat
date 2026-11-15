@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
  echo [X] Pehle INSTALL.bat chalayein.
  pause
  exit /b 1
)
set /p Q=Sawal:
if "%Q%"=="" exit /b 0
".venv\Scripts\python.exe" -m engine ask "%Q%"
echo.
pause
