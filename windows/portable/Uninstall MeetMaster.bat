@echo off
title MeetMaster uninstall
echo.
echo  Removing MeetMaster (per-user; no admin needed)...

REM Ask the running instance to close: killing every pythonw.exe would be
REM rude, so only remove startup + files and tell the user to Exit the tray.
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v MeetMaster /f >nul 2>&1

echo  - auto-start entry removed
echo.
echo  If the tray icon is still showing, right-click it and choose Exit,
echo  then press any key to delete the files.
pause >nul

rmdir /s /q "%LOCALAPPDATA%\Programs\MeetMaster" 2>nul
rmdir /s /q "%APPDATA%\MeetMaster" 2>nul
echo  - files removed
echo.
echo  Done.
pause
