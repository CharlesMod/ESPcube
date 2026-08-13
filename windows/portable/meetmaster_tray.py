"""MeetMaster (portable) — pure-stdlib Windows tray app for the ESPcube.

No PyInstaller, no pip, no admin: this runs under the official embeddable
Python runtime (or any installed Python 3.8+). The tray icon is done with
raw Win32 via ctypes because pystray isn't available without pip — and a
tray icon is only ~200 lines of user32/shell32 anyway.

Right-click menu: status line, checkable "Start with Windows" (HKCU Run,
never needs admin), settings, rediscover, exit. Icon color mirrors the
cube: green free, red on a call, grey unreachable.
"""

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))  # embeddable python doesn't add script dir

from discover import find_cube            # noqa: E402
from mic_detect import mic_in_use, wait_for_mic_change  # noqa: E402
from wsclient import send_text            # noqa: E402

APP_NAME = "MeetMaster"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SETTINGS_PATH = Path(os.environ.get("APPDATA", ".")) / APP_NAME / "settings.json"

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ctypes defaults every restype to c_int, which truncates 64-bit handles
# and pointers — the crash would be intermittent and machine-specific.
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM)
user32.CreateWindowExW.restype = wintypes.HWND
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadIconW.restype = wintypes.HANDLE
user32.CreatePopupMenu.restype = wintypes.HMENU
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.CreateMutexW.restype = wintypes.HANDLE

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_APP_TRAY = 0x8001          # tray callback
WM_APP_STATE = 0x8002         # watcher thread -> UI: state changed
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
MF_GRAYED, MF_CHECKED = 0x0001, 0x0008
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x0002, 0x0100, 0x0080
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010

ID_STATUS, ID_AUTOSTART, ID_SETTINGS, ID_REDISCOVER, ID_EXIT = 1, 2, 3, 4, 5

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16),
                ("hBalloonIcon", wintypes.HICON)]


state = {"on_call": False, "cube": None, "error": None}
settings = {}
icons = {}
hwnd = None
nid = None
stop_event = threading.Event()


# ----------------------------------------------------------------- settings

def load_settings():
    global settings
    try:
        settings = json.loads(SETTINGS_PATH.read_text())
    except Exception:
        settings = {}
    return settings


def save_settings():
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2))


def message_box(text, flags=0x40):
    user32.MessageBoxW(None, text, APP_NAME, flags)


# ---------------------------------------------------------------- autostart

def autostart_command():
    """Re-launch exactly how we're running now: this interpreter, this script.

    pythonw.exe avoids a console window; if we're under python.exe, prefer
    the pythonw.exe sitting next to it when it exists.
    """
    exe = Path(sys.executable)
    quiet = exe.with_name("pythonw.exe")
    if quiet.exists():
        exe = quiet
    return f'"{exe}" "{Path(__file__).resolve()}"'


def is_autostart():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enable):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ,
                                  autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
    except OSError as e:
        state["error"] = f"autostart: {e}"


# --------------------------------------------------------------- cube comms

def send(cmd):
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
            state["cube"] = None  # force rediscovery on the retry
            if attempt == 2:
                state["error"] = f"unreachable: {e}"
                return False
    return False


def watcher():
    last = None
    while not stop_event.is_set():
        try:
            now = mic_in_use()
        except Exception as e:
            state["error"] = f"mic detect: {e}"
            user32.PostMessageW(hwnd, WM_APP_STATE, 0, 0)
            time.sleep(5)
            continue
        if now != last:
            state["on_call"] = now
            send("R" if now else "G")
            user32.PostMessageW(hwnd, WM_APP_STATE, 0, 0)
            last = now
        wait_for_mic_change()
        time.sleep(0.5)


# ---------------------------------------------------------------- tray icon

def load_icons():
    small = user32.GetSystemMetrics(49)  # SM_CXSMICON
    for key, fname in (("free", "meetmaster.ico"),
                       ("call", "meetmaster_red.ico"),
                       ("lost", "meetmaster_grey.ico")):
        path = APP_DIR / fname
        h = user32.LoadImageW(None, str(path), IMAGE_ICON, small, small,
                              LR_LOADFROMFILE)
        icons[key] = h or user32.LoadIconW(None, 32512)  # IDI_APPLICATION


def current_icon():
    if state["error"]:
        return icons["lost"]
    return icons["call"] if state["on_call"] else icons["free"]


def status_text():
    if state["error"]:
        return f"{APP_NAME} — {state['error']}"
    where = (state["cube"] or "").replace("ws://", "").replace("/ws", "")
    return f"{APP_NAME} — {'on a call' if state['on_call'] else 'free'}" + (
        f" ({where})" if where else "")


def tray_update(add=False):
    nid.hIcon = current_icon()
    nid.szTip = status_text()[:127]
    shell32.Shell_NotifyIconW(NIM_ADD if add else NIM_MODIFY,
                              ctypes.byref(nid))


def show_menu():
    menu = user32.CreatePopupMenu()
    user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, ID_STATUS, status_text())
    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
    check = MF_CHECKED if is_autostart() else 0
    user32.AppendMenuW(menu, MF_STRING | check, ID_AUTOSTART,
                       "Start with Windows")
    user32.AppendMenuW(menu, MF_STRING, ID_SETTINGS, "Open settings file")
    user32.AppendMenuW(menu, MF_STRING, ID_REDISCOVER, "Find cube again")
    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
    user32.AppendMenuW(menu, MF_STRING, ID_EXIT, "Exit")

    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    user32.SetForegroundWindow(hwnd)  # required or the menu won't dismiss
    cmd = user32.TrackPopupMenu(
        menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
        pt.x, pt.y, 0, hwnd, None)
    user32.DestroyMenu(menu)

    if cmd == ID_AUTOSTART:
        set_autostart(not is_autostart())
    elif cmd == ID_SETTINGS:
        save_settings()  # make sure the file exists before opening it
        subprocess.Popen(["notepad.exe", str(SETTINGS_PATH)])
        message_box("Settings opened in Notepad.\n\n"
                    "After saving changes, use 'Find cube again' to apply.")
    elif cmd == ID_REDISCOVER:
        load_settings()
        state["cube"] = None
        state["error"] = None
        threading.Thread(
            target=lambda: (send("R" if state["on_call"] else "G"),
                            user32.PostMessageW(hwnd, WM_APP_STATE, 0, 0)),
            daemon=True).start()
    elif cmd == ID_EXIT:
        user32.DestroyWindow(hwnd)


taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")


@WNDPROC
def wnd_proc(h, msg, wparam, lparam):
    if msg == WM_APP_TRAY:
        if lparam in (WM_RBUTTONUP, WM_LBUTTONUP):
            show_menu()
        return 0
    if msg == WM_APP_STATE:
        tray_update()
        return 0
    if msg == taskbar_created:
        tray_update(add=True)  # explorer restarted; re-add our icon
        return 0
    if msg == WM_DESTROY:
        stop_event.set()
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(h, msg, wparam, lparam)


def main():
    global hwnd, nid

    load_settings()
    if not settings.get("token"):
        message_box(
            "No token configured yet.\n\n"
            f"Edit this file and set \"token\":\n{SETTINGS_PATH}\n\n"
            "(The install script normally does this for you.)", 0x30)
        settings.setdefault("token", "")
        settings.setdefault("cube_url", None)
        save_settings()
        subprocess.Popen(["notepad.exe", str(SETTINGS_PATH)])
        return

    # One instance is plenty.
    kernel32.CreateMutexW(None, False, f"{APP_NAME}-single-instance")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return

    wc = WNDCLASSW()
    wc.lpfnWndProc = wnd_proc
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = f"{APP_NAME}TrayWindow"
    user32.RegisterClassW(ctypes.byref(wc))
    hwnd = user32.CreateWindowExW(0, wc.lpszClassName, APP_NAME, 0,
                                  0, 0, 0, 0, None, None, wc.hInstance, None)

    load_icons()
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_APP_TRAY
    tray_update(add=True)

    threading.Thread(target=watcher, daemon=True).start()

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    main()
