"""MeetMaster self-test: proves every fragile layer on THIS machine.

Run it with the bundled runtime (Debug MeetMaster.bat does):
    python\\python.exe app\\selftest.py

Covers, in order: module imports under the embeddable runtime, Win32
struct layouts, real window creation + message dispatch, icon loading, a
tray-add attempt (informational — needs a taskbar), registry round-trip,
settings round-trip, and a full WebSocket send against a loopback server
speaking the cube's protocol. Exit code 0 = every hard check passed.
"""

import ctypes
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILURES = []


def check(name, fn, fatal=True):
    try:
        detail = fn()
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
        return True
    except Exception as e:
        tag = "FAIL" if fatal else "warn"
        print(f"  {tag}  {name}: {e!r}")
        if fatal:
            FAILURES.append(name)
        return False


# ---------------------------------------------------------------- checks

def t_imports():
    global mt, wsclient, mic_detect, discover
    import meetmaster_tray as mt
    import wsclient
    import mic_detect
    import discover
    return f"python {sys.version.split()[0]}"


def t_struct_sizes():
    assert struct.calcsize("P") == 8, "expected 64-bit python"
    n = ctypes.sizeof(mt.NOTIFYICONDATAW)
    assert n == 976, f"NOTIFYICONDATAW sizeof {n}, expected 976"
    w = ctypes.sizeof(mt.WNDCLASSW)
    assert w == 72, f"WNDCLASSW sizeof {w}, expected 72"
    return "NOTIFYICONDATAW=976 WNDCLASSW=72"


received = []
test_hwnd = None


def t_window():
    global test_hwnd
    u = mt.user32
    u.PeekMessageW.restype = wintypes.BOOL
    u.PeekMessageW.argtypes = (ctypes.c_void_p, wintypes.HWND,
                               wintypes.UINT, wintypes.UINT, wintypes.UINT)

    @mt.WNDPROC
    def proc(h, m, w, l):
        if m == 0x8005:
            received.append(int(w))
            return 0
        return u.DefWindowProcW(h, m, w, l)

    globals()["_keep_proc"] = proc  # keep callback alive
    wc = mt.WNDCLASSW()
    wc.lpfnWndProc = proc
    wc.hInstance = mt.kernel32.GetModuleHandleW(None)
    wc.lpszClassName = "MeetMasterSelfTestWnd"
    assert u.RegisterClassW(ctypes.byref(wc)), \
        f"RegisterClassW: {ctypes.get_last_error()}"
    test_hwnd = u.CreateWindowExW(0, wc.lpszClassName, "selftest", 0,
                                  0, 0, 0, 0, None, None, wc.hInstance, None)
    assert test_hwnd, f"CreateWindowExW: {ctypes.get_last_error()}"

    u.PostMessageW(test_hwnd, 0x8005, 42, 0)
    msg = wintypes.MSG()
    deadline = time.time() + 3
    while time.time() < deadline and not received:
        while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.01)
    assert received == [42], f"message dispatch got {received}"
    return f"hwnd=0x{int(test_hwnd):X}, wparam round-trip ok"


def t_icons():
    mt.load_icons()
    missing = [k for k, v in mt.icons.items() if not v]
    assert not missing, f"no handle for {missing}"
    return "green/red/grey handles loaded"


def t_tray_attempt():
    # Needs a live taskbar, so this is informational on headless runners —
    # on a real desktop it must work, which Debug MeetMaster.bat shows.
    nid = mt.NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(mt.NOTIFYICONDATAW)
    nid.hWnd = test_hwnd
    nid.uID = 99
    nid.uFlags = mt.NIF_MESSAGE | mt.NIF_ICON | mt.NIF_TIP
    nid.uCallbackMessage = 0x8006
    nid.hIcon = mt.icons["free"]
    nid.szTip = "MeetMaster selftest"
    ok = mt.shell32.Shell_NotifyIconW(mt.NIM_ADD, ctypes.byref(nid))
    if ok:
        mt.shell32.Shell_NotifyIconW(mt.NIM_DELETE, ctypes.byref(nid))
        return "tray add+remove ok"
    raise RuntimeError(f"NIM_ADD refused ({ctypes.get_last_error()}) — "
                       "expected only if no taskbar (headless session)")


def t_window_cleanup():
    assert mt.user32.DestroyWindow(test_hwnd)
    return None


def t_registry():
    import winreg
    name = "MeetMasterSelfTest"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, mt.RUN_KEY, 0,
                        winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, "selftest")
        val, _ = winreg.QueryValueEx(k, name)
        winreg.DeleteValue(k, name)
    assert val == "selftest"
    return "HKCU Run write/read/delete ok"


def t_settings():
    old_path, old_settings = mt.SETTINGS_PATH, dict(mt.settings)
    try:
        with tempfile.TemporaryDirectory() as d:
            mt.SETTINGS_PATH = Path(d) / "settings.json"
            mt.settings.clear()
            mt.settings.update({"token": "selftest-token", "cube_url": None})
            mt.save_settings()
            mt.settings.clear()
            loaded = mt.load_settings()
            assert loaded.get("token") == "selftest-token"
    finally:
        mt.SETTINGS_PATH = old_path
        mt.settings.clear()
        mt.settings.update(old_settings)
    return None


def t_websocket_loopback():
    """Full client-protocol check against a local server speaking enough
    WebSocket to validate the handshake and unmask one frame."""
    got = {}
    ready = threading.Event()
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        ready.set()
        conn, _ = srv.accept()
        with conn:
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(1024)
            head = data.decode("latin1")
            got["upgrade"] = "upgrade: websocket" in head.lower()
            got["key"] = "sec-websocket-key:" in head.lower()
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\n"
                         b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
            hdr = conn.recv(2)
            length = hdr[1] & 0x7F
            got["masked"] = bool(hdr[1] & 0x80)
            mask = conn.recv(4)
            payload = bytearray(conn.recv(length))
            for i in range(length):
                payload[i] ^= mask[i % 4]
            got["text"] = payload.decode()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    ready.wait(2)
    wsclient.send_text(f"ws://127.0.0.1:{port}/ws", "selftest-token:G")
    t.join(3)
    srv.close()
    assert got.get("upgrade") and got.get("key"), "handshake headers wrong"
    assert got.get("masked"), "client frame not masked (RFC violation)"
    assert got.get("text") == "selftest-token:G", f"payload {got.get('text')!r}"
    return "handshake + masked frame verified byte-for-byte"


def t_mic_detect():
    val = mic_detect.mic_in_use()
    assert isinstance(val, bool)
    return f"mic_in_use() -> {val}"


def t_discover_graceful():
    # Not asserting a cube is present — asserting lookups can't blow up.
    ip = discover.mdns_lookup()
    return f"mdns -> {ip or 'none (fine)'}"


def main():
    print("MeetMaster selftest")
    print("-" * 50)
    check("imports under this runtime", t_imports)
    if FAILURES:  # nothing else can run without the modules
        print("-" * 50)
        print("RESULT: FAIL (imports)")
        return 1
    check("Win32 struct layouts", t_struct_sizes)
    check("window create + message dispatch", t_window)
    check("status icons load", t_icons)
    check("tray icon add (needs taskbar)", t_tray_attempt, fatal=False)
    check("window cleanup", t_window_cleanup)
    check("registry autostart round-trip", t_registry)
    check("settings round-trip", t_settings)
    check("websocket protocol loopback", t_websocket_loopback)
    check("mic detection callable", t_mic_detect)
    check("discovery graceful", t_discover_graceful, fatal=False)
    print("-" * 50)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)}): {', '.join(FAILURES)}")
        return 1
    print("RESULT: PASS — this machine can run MeetMaster")
    return 0


if __name__ == "__main__":
    sys.exit(main())
