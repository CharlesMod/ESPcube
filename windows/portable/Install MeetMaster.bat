@echo off
setlocal EnableDelayedExpansion
title MeetMaster setup
REM Installs per-user: no admin rights needed anywhere in this script.

set "DEST=%LOCALAPPDATA%\Programs\MeetMaster"
set "SETTINGS_DIR=%APPDATA%\MeetMaster"
set "SETTINGS=%SETTINGS_DIR%\settings.json"

echo.
echo  MeetMaster setup
echo  ----------------
echo  Installing to: %DEST%
echo.

REM ---- copy the app (and bundled Python runtime, if this bundle has one)
robocopy "%~dp0app" "%DEST%\app" /MIR /NFL /NDL /NJH /NJS >nul
if exist "%~dp0python" (
  robocopy "%~dp0python" "%DEST%\python" /MIR /NFL /NDL /NJH /NJS >nul
)

REM ---- pick an interpreter: bundled first, then any installed Python
set "PYW="
if exist "%DEST%\python\pythonw.exe" set "PYW=%DEST%\python\pythonw.exe"
if not defined PYW (
  for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do if not defined PYW set "PYW=%%P"
)
if not defined PYW (
  echo  ERROR: no Python found. This bundle should include a "python" folder;
  echo  re-download MeetMaster-portable.zip, or install Python from the
  echo  Microsoft Store and run this again.
  pause
  exit /b 1
)
echo  Using Python: %PYW%

REM ---- token (asked once; kept in %APPDATA%\MeetMaster, not in this folder)
if not exist "%SETTINGS%" (
  echo.
  set /p TOKEN="  Paste the cube's token and press Enter: "
  if not defined TOKEN (
    echo  No token entered - run this installer again when you have it.
    pause
    exit /b 1
  )
  mkdir "%SETTINGS_DIR%" 2>nul
  > "%SETTINGS%" echo {"token": "!TOKEN!", "cube_url": null}
)

REM ---- auto-start for this user only (HKCU never needs admin)
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v MeetMaster ^
  /t REG_SZ /d "\"%PYW%\" \"%DEST%\app\meetmaster_tray.py\"" /f >nul

REM ---- start it now
start "" "%PYW%" "%DEST%\app\meetmaster_tray.py"

echo.
echo  Done. MeetMaster is running in the system tray (bottom-right;
echo  click the ^^ arrow if you don't see it, and drag it out to pin it).
echo  Green = free, red = on a call. Right-click it for options.
echo.
echo  You can remove the USB stick now.
echo.
pause
