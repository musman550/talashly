@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [X] Pehle INSTALL.bat chalayein.
  pause
  exit /b 1
)
set /p CODEPATH=Code folder ka poora path (misal C:\projects\myapp):
if "%CODEPATH%"=="" (
  echo Path khali hai.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m engine index-code "%CODEPATH%"
echo.
echo Ab search mein sawal ke shuru mein  code:  likhein, misal  code: fetch_user_profile
pause
