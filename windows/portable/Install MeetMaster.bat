@echo off
setlocal
title MeetMaster setup
REM Installs per-user: no admin rights needed anywhere in this script.
REM Unattended mode: set MEETMASTER_CI=1 (and MEETMASTER_TOKEN if no
REM settings file exists yet); used by the automated Windows tests.
REM Note: plain setlocal on purpose — delayed expansion would corrupt
REM paths containing '!'. It is enabled only around single lines below.

set "SRC=%~dp0"
set "DEST=%LOCALAPPDATA%\Programs\MeetMaster"
set "SETTINGS_DIR=%APPDATA%\MeetMaster"
set "SETTINGS=%SETTINGS_DIR%\settings.json"

echo.
echo  MeetMaster setup
echo  ----------------
echo  Installing to: "%DEST%"
echo.

REM ---- ask a running copy to exit gracefully (removes its tray icon),
REM      then force-stop anything that ignored us, and WAIT for the exit
set "PYQ="
if exist "%DEST%\python\python.exe" set "PYQ=%DEST%\python\python.exe"
if not defined PYQ if exist "%SRC%python\python.exe" set "PYQ=%SRC%python\python.exe"
if defined PYQ (
  "%PYQ%" -c "import ctypes; u=ctypes.WinDLL('user32'); u.PostMessageW(ctypes.c_void_p(0xFFFF), u.RegisterWindowMessageW('MeetMasterQuitBroadcast'), 0, 0)" >nul 2>&1
  ping -n 3 127.0.0.1 >nul
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" | Where-Object { $_.CommandLine -like '*meetmaster_tray.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -PassThru } | Wait-Process -Timeout 10 -ErrorAction SilentlyContinue" >nul 2>&1

REM ---- copy the app (scripts + icons are never file-locked)
robocopy "%SRC%app" "%DEST%\app" /MIR /NFL /NDL /NJH /NJS >nul
if errorlevel 8 (
  echo  ERROR: could not copy the app files from "%SRC%app".
  echo  Is the "app" folder next to this script?
  if not defined MEETMASTER_CI pause
  exit /b 1
)

REM ---- copy the bundled Python runtime only if not already present
REM      (an in-use runtime can't be overwritten, and it never changes)
if exist "%DEST%\python\pythonw.exe" (
  echo  Existing Python runtime found - keeping it.
) else (
  if exist "%SRC%python" (
    robocopy "%SRC%python" "%DEST%\python" /MIR /NFL /NDL /NJH /NJS >nul
    if errorlevel 8 (
      echo  ERROR: could not copy the Python runtime from "%SRC%python".
      if not defined MEETMASTER_CI pause
      exit /b 1
    )
  )
)

REM ---- pick an interpreter: bundled first, then any installed Python
set "PYW="
if exist "%DEST%\python\pythonw.exe" set "PYW=%DEST%\python\pythonw.exe"
if not defined PYW (
  for /f "delims=" %%P in ('where pythonw.exe 2^>nul ^| findstr /v /i "WindowsApps"') do if not defined PYW set "PYW=%%P"
)
if not defined PYW (
  echo  ERROR: no Python found. This bundle should include a "python" folder;
  echo  re-download MeetMaster-portable.zip, or install Python from the
  echo  Microsoft Store and run this again.
  if not defined MEETMASTER_CI pause
  exit /b 1
)
echo  Using Python: "%PYW%"

REM ---- token (asked once; kept in APPDATA, survives updates)
if exist "%SETTINGS%" goto have_settings
set "TOKEN="
if not defined MEETMASTER_CI goto ask_token
if defined MEETMASTER_TOKEN set "TOKEN=%MEETMASTER_TOKEN%"
goto check_token
:ask_token
set /p TOKEN="  Paste the cube's token and press Enter: "
:check_token
if not defined TOKEN (
  echo  No token entered - run this installer again when you have it.
  if not defined MEETMASTER_CI pause
  exit /b 1
)
mkdir "%SETTINGS_DIR%" 2>nul
setlocal EnableDelayedExpansion
> "!SETTINGS!" echo {"token": "!TOKEN!", "cube_url": null}
endlocal
:have_settings

REM ---- auto-start for this user only (HKCU never needs admin).
REM      Delayed expansion here so paths with & or ! can't break the line.
setlocal EnableDelayedExpansion
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v MeetMaster ^
  /t REG_SZ /d "\"!PYW!\" \"!DEST!\app\meetmaster_tray.py\"" /f >nul
endlocal

REM ---- start it now
start "" "%PYW%" "%DEST%\app\meetmaster_tray.py"

echo.
echo  Done. MeetMaster is running in the system tray (bottom-right;
echo  click the ^^ arrow if you don't see it, and drag it out to pin it).
echo  Green = free, red = on a call. Right-click it for options.
echo.
echo  If anything seems wrong, run "Debug MeetMaster.bat" from this
echo  folder - it prints exactly what is happening.
echo.
echo  You can remove the USB stick now.
echo.
if not defined MEETMASTER_CI pause
exit /b 0
