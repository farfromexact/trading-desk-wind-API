@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
set "deskExit=%ERRORLEVEL%"
if not "%WIND_DESK_NO_PAUSE%"=="1" pause
exit /b %deskExit%
