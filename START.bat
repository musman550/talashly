@echo off
setlocal
cd /d "%~dp0"
title Talashly - band karne ke liye Ctrl+C

if not exist ".venv\Scripts\python.exe" (
  echo [X] Pehle INSTALL.bat chalayein.
  pause
  exit /b 1
)

set "PYTHONUTF8=1"
set "OPEN=--open"
set /a RESTARTS=0

:loop
".venv\Scripts\python.exe" -m engine serve %OPEN%
set "RC=%errorlevel%"
set "OPEN="
if "%RC%"=="0" goto end
if "%RC%"=="2" goto end
set /a RESTARTS+=1
if %RESTARTS% GTR 50 (
  echo [X] Server bar bar gir raha hai. DOCTOR.bat chalayein.
  goto end
)
echo.
echo [!] Server band ho gaya, code %RC%. 5 second mein khud dobara shuru ho raha hai... %RESTARTS%
timeout /t 5 /nobreak >nul
goto loop

:end
echo.
pause
