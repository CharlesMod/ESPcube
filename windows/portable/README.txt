MeetMaster (portable)
=====================

Turns the ESPcube red while your microphone is open, green when it isn't.

This bundle needs NO admin rights and installs NO programs system-wide.
It runs on plain Python — the official Windows Python runtime is included
in the "python" folder (python.exe is signed by the Python Software
Foundation, so it passes policies that block unsigned packed .exe files).

Quick start
-----------
1. Double-click "Install MeetMaster.bat"
2. Paste the cube's token when asked (once)
3. Look for the small green square in the system tray (bottom-right —
   click the ^ arrow if it's hidden, and drag the icon out to pin it)

That's it. It starts with Windows from now on. You can pull the USB stick.

The tray icon IS the status:
   green = free
   red   = on a call (any app using the microphone)
   grey  = can't reach the cube

Right-click the icon for:
   Start with Windows   (checkbox — turn auto-start on/off)
   Open settings file   (change the token or pin a cube address)
   Find cube again      (after a router reboot etc.)
   Exit

Where things live (all per-user, delete-safe):
   app:      %LOCALAPPDATA%\Programs\MeetMaster
   settings: %APPDATA%\MeetMaster\settings.json
   startup:  HKCU\Software\Microsoft\Windows\CurrentVersion\Run -> MeetMaster

"Run without installing.bat" runs it straight from this folder instead —
useful for a quick test, but it dies if the folder is a USB stick and you
remove it, and it won't start with Windows.

"Uninstall MeetMaster.bat" removes the auto-start entry and the files.

If the company laptop already has Python installed, this also works with
it automatically (the scripts prefer the bundled runtime, then fall back
to any pythonw.exe on PATH). Nothing here needs pip.
