"""Ask a running MeetMaster to exit, exactly as a newer instance would."""
import ctypes

user32 = ctypes.WinDLL("user32")
msg = user32.RegisterWindowMessageW("MeetMasterQuitBroadcast")
user32.PostMessageW(ctypes.c_void_p(0xFFFF), msg, 0, 0)  # HWND_BROADCAST
print(f"quit broadcast sent (message id {msg})")
