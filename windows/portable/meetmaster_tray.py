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
LOG_PATH = SETTINGS_PATH.with_name("meetmaster.log")


def log(message):
    """Under pythonw there is no console, so failures must go to a file —
    otherwise a startup crash looks like 'nothing happened'."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")
    except OSError:
        pass


def log_exception(where):
    import traceback
    log(f"EXCEPTION in {where}:\n{traceback.format_exc()}")

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ctypes defaults BOTH restype and argtypes to 32-bit c_int. On 64-bit
# Windows, handles (HWND, HINSTANCE ≈ 0x7FF6...) don't fit in 32 bits, so
# without full declarations they get truncated going IN as well as coming
# OUT — CreateWindowExW quietly gets a garbage hInstance and the tray icon
# never appears. Declare everything.
LRESULT = ctypes.c_ssize_t
LPVOID = ctypes.c_void_p

def _sig(fn, restype, *argtypes):
    fn.restype = restype
    fn.argtypes = argtypes

_sig(user32.DefWindowProcW, LRESULT,
     wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32.CreateWindowExW, wintypes.HWND,
     wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, LPVOID)
_sig(user32.DestroyWindow, wintypes.BOOL, wintypes.HWND)
_sig(user32.GetMessageW, ctypes.c_int,
     LPVOID, wintypes.HWND, wintypes.UINT, wintypes.UINT)
_sig(user32.TranslateMessage, wintypes.BOOL, LPVOID)
_sig(user32.DispatchMessageW, LRESULT, LPVOID)
_sig(user32.PostMessageW, wintypes.BOOL,
     wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32.PostQuitMessage, None, ctypes.c_int)
_sig(user32.CreatePopupMenu, wintypes.HMENU)
_sig(user32.AppendMenuW, wintypes.BOOL,
     wintypes.HMENU, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR)
_sig(user32.TrackPopupMenu, ctypes.c_int,
     wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
     ctypes.c_int, wintypes.HWND, LPVOID)
_sig(user32.DestroyMenu, wintypes.BOOL, wintypes.HMENU)
_sig(user32.GetCursorPos, wintypes.BOOL, LPVOID)
_sig(user32.SetForegroundWindow, wintypes.BOOL, wintypes.HWND)
_sig(user32.LoadImageW, wintypes.HANDLE,
     wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
     ctypes.c_int, ctypes.c_int, wintypes.UINT)
_sig(user32.LoadIconW, wintypes.HICON, wintypes.HINSTANCE, LPVOID)
_sig(user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
_sig(user32.MessageBoxW, ctypes.c_int,
     wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
_sig(user32.RegisterWindowMessageW, wintypes.UINT, wintypes.LPCWSTR)
_sig(user32.RegisterClassW, wintypes.WORD, LPVOID)
_sig(shell32.Shell_NotifyIconW, wintypes.BOOL, wintypes.DWORD, LPVOID)
_sig(kernel32.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)
_sig(kernel32.CreateMutexW, wintypes.HANDLE,
     LPVOID, wintypes.BOOL, wintypes.LPCWSTR)

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
HWND_BROADCAST = 0xFFFF

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
    # The whole body is guarded: this thread must survive anything — a
    # watcher that dies silently means the cube lies about the mic.
    last = None
    while not stop_event.is_set():
        try:
            now = mic_in_use()
            if now != last:
                state["on_call"] = now
                send("R" if now else "G")
                user32.PostMessageW(hwnd, WM_APP_STATE, 0, 0)
                last = now
            wait_for_mic_change()
            time.sleep(0.5)
        except Exception as e:
            state["error"] = f"watcher: {e}"
            log_exception("watcher")
            user32.PostMessageW(hwnd, WM_APP_STATE, 0, 0)
            time.sleep(5)


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
    op = NIM_ADD if add else NIM_MODIFY
    ok = shell32.Shell_NotifyIconW(op, ctypes.byref(nid))
    if not ok and not add:
        # Self-heal: MODIFY fails if our icon isn't registered (taskbar came
        # up after we started, or explorer restarted without the message
        # reaching us yet) — fall back to ADD.
        ok = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
    if not ok:
        log(f"Shell_NotifyIcon failed (op={op}): {ctypes.get_last_error()}")


def tray_add_initial():
    """First NIM_ADD, with a couple of retries.

    At logon we can start before the taskbar exists, in which case NIM_ADD
    fails; the TaskbarCreated broadcast re-adds us later regardless, so
    failing here is degraded UX, never fatal — the watcher runs either way.
    """
    for attempt in (1, 2, 3):
        nid.hIcon = current_icon()
        nid.szTip = status_text()[:127]
        if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            log("tray icon added")
            return True
        log(f"NIM_ADD attempt {attempt} failed: {ctypes.get_last_error()}")
        time.sleep(2)
    log("tray icon not added yet; waiting on TaskbarCreated (watcher runs)")
    return False


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
    # WM_NULL after TrackPopupMenu: second half of the documented KB135788
    # workaround — without it the next menu open can flash and self-dismiss.
    user32.PostMessageW(hwnd, 0, 0, 0)
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
# A newer instance broadcasts this to ask an older one to exit, so the
# installer can upgrade over a running copy without taskkill heroics.
quit_broadcast = user32.RegisterWindowMessageW("MeetMasterQuitBroadcast")


@WNDPROC
def wnd_proc(h, msg, wparam, lparam):
    try:
        return _wnd_proc(h, msg, wparam, lparam)
    except Exception:
        # An exception escaping a ctypes callback corrupts nothing but is
        # otherwise swallowed — log it so it's diagnosable.
        log_exception("wnd_proc")
        return user32.DefWindowProcW(h, msg, wparam, lparam)


def _wnd_proc(h, msg, wparam, lparam):
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
    if msg == quit_broadcast:
        log("quit broadcast received; exiting (upgrade or uninstall)")
        user32.DestroyWindow(h)
        return 0
    if msg == WM_DESTROY:
        log("WM_DESTROY: removing tray icon and quitting")
        stop_event.set()
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(h, msg, wparam, lparam)


def main():
    global hwnd, nid

    log(f"starting: python {sys.version.split()[0]} at {sys.executable}")
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

    # One instance is plenty — but never refuse to start: a wedged older
    # instance (e.g. one that failed before creating its window) can't
    # receive the quit broadcast, and exiting here would leave the user
    # with nothing. Ask politely, wait, then take over.
    kernel32.CreateMutexW(None, False, f"{APP_NAME}-v2-single-instance")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        log("another instance detected; broadcasting quit and taking over")
        user32.PostMessageW(HWND_BROADCAST, quit_broadcast, 0, 0)
        time.sleep(2.5)

    wc = WNDCLASSW()
    wc.lpfnWndProc = wnd_proc
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = f"{APP_NAME}TrayWindow"
    if not user32.RegisterClassW(ctypes.byref(wc)):
        log(f"RegisterClassW failed: {ctypes.get_last_error()}")
    hwnd = user32.CreateWindowExW(0, wc.lpszClassName, APP_NAME, 0,
                                  0, 0, 0, 0, None, None, wc.hInstance, None)
    if not hwnd:
        err = ctypes.get_last_error()
        log(f"CreateWindowExW failed: {err}")
        message_box(f"Could not create the tray window (error {err}).\n"
                    f"See {LOG_PATH}", 0x10)
        return

    load_icons()
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_APP_TRAY
    tray_add_initial()
    log("entering message loop")

    threading.Thread(target=watcher, daemon=True).start()

    msg = wintypes.MSG()
    while True:
        ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if ret <= 0:
            # 0 = WM_QUIT (normal exit), -1 = error. Either way, say so —
            # a silent process exit is undiagnosable from the outside.
            log(f"message loop exited (GetMessageW returned {ret}, "
                f"err={ctypes.get_last_error()})")
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_exception("main")
        raise
