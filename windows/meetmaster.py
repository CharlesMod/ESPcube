"""MeetMaster — Windows tray app that turns the ESPcube red while you're on a call.

Double-clicking the exe installs it: it copies itself to LocalAppData,
registers auto-start, and drops into the system tray. Right-click the tray
icon for a checkable auto-start toggle and an exit item.

Detection is event-driven (see mic_detect.py) — the watcher thread sleeps
in the kernel until Windows reports a microphone state change.
"""

import json
import os
import sys
import shutil
import subprocess
import threading
import time
from pathlib import Path

import winreg
from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem

# Running from source (not frozen) needs the shared host modules on the path.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))

from discover import find_cube          # noqa: E402
from mic_detect import mic_in_use, wait_for_mic_change  # noqa: E402
from wsclient import send_text          # noqa: E402

APP_NAME = "MeetMaster"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", ".")) / "Programs" / APP_NAME
SETTINGS_PATH = Path(os.environ.get("APPDATA", ".")) / APP_NAME / "settings.json"

state = {"on_call": False, "cube": None, "error": None}


# ---------------------------------------------------------------- settings

def load_settings():
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except Exception:
        return {}


def save_settings(data):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2))


def ask_token(current=""):
    """First-run prompt. tkinter ships with Python, so no extra dependency."""
    import tkinter as tk
    from tkinter import simpledialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    value = simpledialog.askstring(
        f"{APP_NAME} setup",
        "Paste the cube's token\n(the wsToken value from the firmware's secrets.h):",
        initialvalue=current, parent=root)
    root.destroy()
    return (value or "").strip()


# ---------------------------------------------------------------- autostart

def is_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enable):
    target = str(installed_exe())
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, f'"{target}"')
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
    except OSError as e:
        state["error"] = f"autostart: {e}"


def installed_exe():
    return INSTALL_DIR / f"{APP_NAME}.exe"


def running_frozen():
    return getattr(sys, "frozen", False)


def install_self():
    """Copy the exe into LocalAppData and relaunch from there.

    Returns True if we handed off to the installed copy and should exit.
    Running from the USB stick would otherwise break the moment it's pulled.
    """
    if not running_frozen():
        return False
    here = Path(sys.executable).resolve()
    target = installed_exe()
    if here == target.resolve():
        return False  # already the installed copy

    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(here, target)
    except OSError as e:
        notify_box(f"Could not install to:\n{target}\n\n{e}")
        return False

    set_autostart(True)
    subprocess.Popen([str(target)], close_fds=True)
    notify_box(
        f"{APP_NAME} is installed and now running in your system tray "
        f"(bottom-right, you may need to click the ^ arrow).\n\n"
        f"It will start automatically with Windows. Right-click the tray "
        f"icon to change that or to quit.\n\nYou can remove the USB stick.")
    return True


def notify_box(message):
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x40)


# ---------------------------------------------------------------- tray icon

def make_icon(color):
    """A rounded square in the current status color, drawn at runtime."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6, 6, 58, 58], radius=14, fill=color)
    d.rounded_rectangle([6, 6, 58, 58], radius=14, outline=(255, 255, 255, 90),
                        width=3)
    return img


COLORS = {"call": (216, 38, 43, 255), "free": (29, 157, 63, 255),
          "lost": (120, 124, 130, 255)}


def refresh_icon(icon):
    if state["error"]:
        icon.icon = make_icon(COLORS["lost"])
    else:
        icon.icon = make_icon(COLORS["call" if state["on_call"] else "free"])
    icon.title = status_text()


def status_text():
    if state["error"]:
        return f"{APP_NAME} — {state['error']}"
    where = state["cube"] or "searching…"
    return f"{APP_NAME} — {'on a call' if state['on_call'] else 'free'} ({where})"


# ---------------------------------------------------------------- watcher

def send(cmd, settings):
    """Send one command, rediscovering once if the cached address is stale."""
    for attempt in (1, 2):
        try:
            if not state["cube"]:
                state["cube"] = find_cube(verbose=False,
                                          explicit=settings.get("cube_url"))
            send_text(state["cube"], f"{settings['token']}:{cmd}")
            state["error"] = None
            return True
        except SystemExit:
            state["error"] = "cube not found on this network"
            state["cube"] = None
            return False
        except Exception as e:
            state["cube"] = None          # force rediscovery on the retry
            if attempt == 2:
                state["error"] = f"unreachable: {e}"
                return False
    return False


def watcher(icon, settings, stop):
    last = None
    while not stop.is_set():
        try:
            now = mic_in_use()
        except Exception as e:
            state["error"] = f"mic detect: {e}"
            refresh_icon(icon)
            time.sleep(5)
            continue

        if now != last:
            state["on_call"] = now
            send("R" if now else "G", settings)
            refresh_icon(icon)
            last = now

        wait_for_mic_change()
        time.sleep(0.5)   # let the OS settle before re-reading


# ---------------------------------------------------------------- entry

def main():
    if install_self():
        return  # handed off to the installed copy

    settings = load_settings()
    if not settings.get("token"):
        token = ask_token()
        if not token:
            notify_box("No token entered — nothing to do.\n"
                       "Run MeetMaster again when you have it.")
            return
        settings["token"] = token
        settings.setdefault("cube_url", None)
        save_settings(settings)

    stop = threading.Event()

    def on_quit(icon):
        stop.set()
        icon.stop()

    def toggle_autostart(icon, item):
        set_autostart(not is_autostart())
        icon.update_menu()

    def change_token(icon):
        value = ask_token(settings.get("token", ""))
        if value:
            settings["token"] = value
            save_settings(settings)

    def find_again(icon):
        state["cube"] = None
        state["error"] = None
        send("G" if not state["on_call"] else "R", settings)
        refresh_icon(icon)

    menu = Menu(
        MenuItem(lambda i: status_text(), None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("Start with Windows", toggle_autostart,
                 checked=lambda i: is_autostart()),
        MenuItem("Set token…", change_token),
        MenuItem("Find cube again", find_again),
        Menu.SEPARATOR,
        MenuItem("Exit", on_quit),
    )

    icon = Icon(APP_NAME, make_icon(COLORS["free"]), status_text(), menu)
    threading.Thread(target=watcher, args=(icon, settings, stop),
                     daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
