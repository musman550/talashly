@echo off
setlocal
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
del "%STARTUP%\Talashly.bat" >nul 2>nul
echo [OK] Autostart band kar diya.
pause
