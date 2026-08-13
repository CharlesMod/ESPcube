@echo off
setlocal
title MeetMaster debug
REM Everything on screen. If the tray icon isn't appearing, run this and
REM photograph the window - it says exactly which layer is failing.

set "PY="
if exist "%~dp0python\python.exe" set "PY=%~dp0python\python.exe"
if not defined PY (
  REM findstr filters out the Microsoft Store's fake python.exe alias stub,
  REM which "runs" but only prints an install-from-Store advert.
  for /f "delims=" %%P in ('where python.exe 2^>nul ^| findstr /v /i "WindowsApps"') do if not defined PY set "PY=%%P"
)
if not defined PY (
  echo No real python.exe found - the bundle's "python" folder is missing
  echo and no system Python is installed. Re-download the full
  echo MeetMaster-portable.zip so the python folder is present.
  pause
  exit /b 1
)

echo Using: "%PY%"
echo Log file: "%APPDATA%\MeetMaster\meetmaster.log"
echo.
echo ==== Step 1: self-test =====================================
"%PY%" "%~dp0app\selftest.py"
echo.
echo ==== Step 2: run the app with visible errors ===============
echo (Close its tray icon via right-click - Exit, or press Ctrl+C here.)
"%PY%" -X faulthandler "%~dp0app\meetmaster_tray.py"
echo ------------------------------------------------------------
echo Exited with code %errorlevel%.
if exist "%APPDATA%\MeetMaster\meetmaster.log" (
  echo Last log lines:
  powershell -NoProfile -Command "Get-Content -Tail 15 \"$env:APPDATA\MeetMaster\meetmaster.log\"" 2>nul
)
pause
