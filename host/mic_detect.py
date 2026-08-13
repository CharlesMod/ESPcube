"""Cross-platform, event-driven microphone-in-use detection.

Exposes two functions, selected by platform at import time:

    mic_in_use()          -> bool   Is any application recording right now?
    wait_for_mic_change() -> None   Block until the mic state may have changed.

Backends:
    Windows  CapabilityAccessManager consent store (the same data behind the
             taskbar mic indicator) + RegNotifyChangeKeyValue as the wakeup.
    macOS    CoreAudio kAudioDevicePropertyDeviceIsRunningSomewhere on the
             default input device + AudioObjectAddPropertyListener as the
             wakeup. No third-party dependencies (ctypes only).
    Linux    PulseAudio/PipeWire recording streams via `pactl list
             source-outputs` (monitor sources excluded) + `pactl subscribe`
             as the wakeup. Works on both pulseaudio and pipewire-pulse.

All backends are push-based: the waiting thread sleeps until the OS reports a
change, with a timeout as a belt-and-braces fallback.
"""

import sys

# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------
if sys.platform == "win32":
    import ctypes
    import time
    import winreg

    # Each app that uses the mic gets a subkey here; LastUsedTimeStop == 0
    # while that app currently has the mic open.
    _MIC_KEY = (r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                r"\CapabilityAccessManager\ConsentStore\microphone")
    _REG_NOTIFY_CHANGE_NAME = 0x1
    _REG_NOTIFY_CHANGE_LAST_SET = 0x4

    _advapi32 = ctypes.windll.advapi32

    def mic_in_use():
        def key_active(path):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                    stop, _ = winreg.QueryValueEx(k, "LastUsedTimeStop")
                    return stop == 0
            except OSError:
                return False

        # Packaged (store) apps sit directly under _MIC_KEY, win32 apps like
        # Zoom under _MIC_KEY\NonPackaged.
        for base in (_MIC_KEY, _MIC_KEY + r"\NonPackaged"):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as k:
                    for i in range(winreg.QueryInfoKey(k)[0]):
                        sub = winreg.EnumKey(k, i)
                        if sub == "NonPackaged":
                            continue
                        if key_active(base + "\\" + sub):
                            return True
            except OSError:
                pass
        return False

    def wait_for_mic_change():
        # Blocks in the kernel until anything under the consent key changes.
        # If the key is unavailable (unusual SKU, policy), degrade to slow
        # polling instead of letting the watcher thread die.
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MIC_KEY) as k:
                _advapi32.RegNotifyChangeKeyValue(
                    k.handle, True,
                    _REG_NOTIFY_CHANGE_NAME | _REG_NOTIFY_CHANGE_LAST_SET,
                    None, False)
        except OSError:
            time.sleep(5)

# --------------------------------------------------------------------------
# macOS
# --------------------------------------------------------------------------
elif sys.platform == "darwin":
    import ctypes
    import ctypes.util
    import struct
    import threading

    _core = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))

    def _fourcc(code):
        return struct.unpack(">I", code.encode("ascii"))[0]

    _kSystemObject = 1                              # kAudioObjectSystemObject
    _kDefaultInput = _fourcc("dIn ")                # kAudioHardwarePropertyDefaultInputDevice
    _kRunningSomewhere = _fourcc("gone")            # kAudioDevicePropertyDeviceIsRunningSomewhere
    _kScopeGlobal = _fourcc("glob")                 # kAudioObjectPropertyScopeGlobal
    _kRunLoop = _fourcc("rnlp")                     # kAudioHardwarePropertyRunLoop

    class _PropAddress(ctypes.Structure):
        _fields_ = [("mSelector", ctypes.c_uint32),
                    ("mScope", ctypes.c_uint32),
                    ("mElement", ctypes.c_uint32)]

    _LISTENER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_uint32,
                                 ctypes.c_uint32, ctypes.c_void_p,
                                 ctypes.c_void_p)

    _changed = threading.Event()

    @_LISTENER
    def _on_change(obj_id, n_addrs, addrs, client_data):
        _changed.set()
        return 0

    def _addr(selector):
        return _PropAddress(selector, _kScopeGlobal, 0)

    def _get_u32(obj_id, selector):
        addr = _addr(selector)
        size = ctypes.c_uint32(4)
        val = ctypes.c_uint32(0)
        status = _core.AudioObjectGetPropertyData(
            ctypes.c_uint32(obj_id), ctypes.byref(addr), 0, None,
            ctypes.byref(size), ctypes.byref(val))
        return val.value if status == 0 else 0

    def _default_input():
        return _get_u32(_kSystemObject, _kDefaultInput)

    _initialized = False
    _listening_dev = None

    def _ensure_listeners():
        global _initialized, _listening_dev
        if not _initialized:
            # Give the HAL its own notification thread so listeners fire
            # without us pumping a CFRunLoop.
            null_loop = ctypes.c_void_p(None)
            addr = _addr(_kRunLoop)
            _core.AudioObjectSetPropertyData(
                ctypes.c_uint32(_kSystemObject), ctypes.byref(addr), 0, None,
                ctypes.sizeof(null_loop), ctypes.byref(null_loop))
            # Wake up if the default input device itself changes (new mic
            # plugged in, AirPods connect, ...).
            addr = _addr(_kDefaultInput)
            _core.AudioObjectAddPropertyListener(
                ctypes.c_uint32(_kSystemObject), ctypes.byref(addr),
                _on_change, None)
            _initialized = True

        dev = _default_input()
        if dev and dev != _listening_dev:
            addr = _addr(_kRunningSomewhere)
            _core.AudioObjectAddPropertyListener(
                ctypes.c_uint32(dev), ctypes.byref(addr), _on_change, None)
            _listening_dev = dev

    def mic_in_use():
        dev = _default_input()
        return bool(dev) and _get_u32(dev, _kRunningSomewhere) != 0

    def wait_for_mic_change():
        _ensure_listeners()
        # The 60 s timeout is only a safety net for a missed notification.
        _changed.wait(timeout=60)
        _changed.clear()

# --------------------------------------------------------------------------
# Linux (PulseAudio / PipeWire)
# --------------------------------------------------------------------------
else:
    import subprocess
    import time

    _subscribe_proc = None

    def _pactl(*args):
        try:
            return subprocess.run(["pactl", *args], capture_output=True,
                                  text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _monitor_source_ids():
        # Monitor sources record a sink's output (loopback), not the mic.
        ids = set()
        for line in _pactl("list", "short", "sources").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1].endswith(".monitor"):
                ids.add(parts[0])
        return ids

    def mic_in_use():
        monitors = _monitor_source_ids()
        for line in _pactl("list", "short", "source-outputs").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1] not in monitors:
                return True
        return False

    def wait_for_mic_change():
        global _subscribe_proc
        while True:
            if _subscribe_proc is None or _subscribe_proc.poll() is not None:
                try:
                    _subscribe_proc = subprocess.Popen(
                        ["pactl", "subscribe"],
                        stdout=subprocess.PIPE, text=True)
                except OSError:
                    time.sleep(5)  # pactl missing / sound server down
                    return
            line = _subscribe_proc.stdout.readline()
            if not line:  # sound server restarted; reconnect
                time.sleep(1)
                continue
            # e.g. "Event 'new' on source-output #71"
            if "source-output" in line:
                return
