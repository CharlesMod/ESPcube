@echo off
setlocal
title MeetMaster uninstall
echo.
echo  Removing MeetMaster (per-user; no admin needed)...

set "DEST=%LOCALAPPDATA%\Programs\MeetMaster"

REM ---- ask nicely first (lets the app remove its tray icon), then force,
REM      and WAIT for the process to actually exit before deleting files
if exist "%DEST%\python\python.exe" (
  "%DEST%\python\python.exe" -c "import ctypes; u=ctypes.WinDLL('user32'); u.PostMessageW(ctypes.c_void_p(0xFFFF), u.RegisterWindowMessageW('MeetMasterQuitBroadcast'), 0, 0)" >nul 2>&1
  ping -n 3 127.0.0.1 >nul
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" | Where-Object { $_.CommandLine -like '*meetmaster_tray.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -PassThru } | Wait-Process -Timeout 10 -ErrorAction SilentlyContinue" >nul 2>&1

reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v MeetMaster /f >nul 2>&1
rmdir /s /q "%DEST%" 2>nul
rmdir /s /q "%APPDATA%\MeetMaster" 2>nul

echo  - stopped the app
echo  - removed the auto-start entry
if exist "%DEST%" (
  echo  WARNING: some files could not be removed - still in use?
  echo  Delete this folder manually, after a reboot if needed:
  echo    "%DEST%"
) else (
  echo  - removed the files and settings
)
echo.
echo  Done.
if not defined MEETMASTER_CI pause
exit /b 0
