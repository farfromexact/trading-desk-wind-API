@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-v2.ps1" %*
set "V2_EXIT=%ERRORLEVEL%"
if not "%V2_EXIT%"=="0" if not "%WIND_DESK_NO_PAUSE%"=="1" pause
exit /b %V2_EXIT%
