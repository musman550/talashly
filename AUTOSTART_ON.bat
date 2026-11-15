@echo off
setlocal
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
> "%STARTUP%\Talashly.bat" echo @echo off
>> "%STARTUP%\Talashly.bat" echo start "" /min "%~dp0START.bat"
if exist "%STARTUP%\Talashly.bat" (
  echo [OK] Ab Windows login par Talashly khud shuru hoga ^(chhoti window mein^).
) else (
  echo [X] Startup entry nahi ban saki.
)
echo Band karne ke liye AUTOSTART_OFF.bat chalayein.
pause
