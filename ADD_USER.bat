@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [X] Pehle INSTALL.bat chalayein.
  pause
  exit /b 1
)
set /p NAME=Naye user ka naam:
if "%NAME%"=="" (
  echo Naam khali hai.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m engine adduser "%NAME%"
echo.
echo Is key ko save kar lein: dobara nahi dikhai jayegi.
echo (Key use karne ke liye: config.toml mein require_key = true karein.)
pause
