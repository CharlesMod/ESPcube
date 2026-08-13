@echo off
setlocal
title MeetMaster (portable)
REM Runs straight from this folder - nothing is copied, no auto-start is set.
REM Note: if this folder is a USB stick, MeetMaster stops when you pull it.
REM Plain setlocal on purpose - delayed expansion would corrupt paths
REM containing '!'; it is enabled only around the settings write below.

set "SRC=%~dp0"
set "SETTINGS_DIR=%APPDATA%\MeetMaster"
set "SETTINGS=%SETTINGS_DIR%\settings.json"

set "PYW="
if exist "%SRC%python\pythonw.exe" set "PYW=%SRC%python\pythonw.exe"
if not defined PYW (
  for /f "delims=" %%P in ('where pythonw.exe 2^>nul ^| findstr /v /i "WindowsApps"') do if not defined PYW set "PYW=%%P"
)
if not defined PYW (
  echo ERROR: no Python found - use the full bundle or install Python.
  pause
  exit /b 1
)

if exist "%SETTINGS%" goto have_settings
set "TOKEN="
set /p TOKEN="Paste the cube's token and press Enter: "
if not defined TOKEN exit /b 1
mkdir "%SETTINGS_DIR%" 2>nul
setlocal EnableDelayedExpansion
> "!SETTINGS!" echo {"token": "!TOKEN!", "cube_url": null}
endlocal
:have_settings

start "" "%PYW%" "%SRC%app\meetmaster_tray.py"
