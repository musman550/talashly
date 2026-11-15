@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [X] Pehle INSTALL.bat chalayein.
  pause
  exit /b 1
)
echo Ye aap ke maujooda database ko sab se naye SAHI backup se badal dega.
echo (Maujooda file data\index.db.corrupt-... naam se mehfooz rahegi.)
echo Pehle START.bat wali window band kar dein.
set /p OK=Jaari rakhne ke liye YES likhein: 
if /i not "%OK%"=="YES" exit /b 0
".venv\Scripts\python.exe" -m engine restore
pause
