@echo off
setlocal EnableDelayedExpansion
title MeetMaster (portable)
REM Runs straight from this folder - nothing is copied, no auto-start is set.
REM Note: if this folder is a USB stick, MeetMaster stops when you pull it.

set "SETTINGS_DIR=%APPDATA%\MeetMaster"
set "SETTINGS=%SETTINGS_DIR%\settings.json"

set "PYW="
if exist "%~dp0python\pythonw.exe" set "PYW=%~dp0python\pythonw.exe"
if not defined PYW (
  for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do if not defined PYW set "PYW=%%P"
)
if not defined PYW (
  echo ERROR: no Python found - use the full bundle or install Python.
  pause
  exit /b 1
)

if not exist "%SETTINGS%" (
  set /p TOKEN="Paste the cube's token and press Enter: "
  if not defined TOKEN exit /b 1
  mkdir "%SETTINGS_DIR%" 2>nul
  > "%SETTINGS%" echo {"token": "!TOKEN!", "cube_url": null}
)

start "" "%PYW%" "%~dp0app\meetmaster_tray.py"
