@echo off
setlocal
title MeetMaster debug
REM Runs the app with a visible console so errors print to the screen.
REM If the tray icon isn't appearing, run this and read what it says.

set "PY="
if exist "%~dp0python\python.exe" set "PY=%~dp0python\python.exe"
if not defined PY (
  for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined PY set "PY=%%P"
)
if not defined PY (
  echo No python.exe found.
  pause
  exit /b 1
)

echo Using: %PY%
echo Log file: %APPDATA%\MeetMaster\meetmaster.log
echo ------------------------------------------------------------
"%PY%" -X faulthandler "%~dp0app\meetmaster_tray.py"
echo ------------------------------------------------------------
echo Exited with code %errorlevel%.
if exist "%APPDATA%\MeetMaster\meetmaster.log" (
  echo Last log lines:
  powershell -NoProfile -Command "Get-Content -Tail 15 \"$env:APPDATA\MeetMaster\meetmaster.log\"" 2>nul
)
pause
