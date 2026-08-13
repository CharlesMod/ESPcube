"""ESPcube headless/console variant: same watcher, no tray icon.

Useful for testing the detection backend on a new machine, and as the
PyInstaller build target.
"""

import time

from websocket import create_connection

from config import CUBE_URL, TOKEN
from mic_detect import mic_in_use, wait_for_mic_change

print("Watching the mic (event-driven, no polling)...")


def send_command(mic_active):
    command = 'R' if mic_active else 'G'

    if command == 'R':
        print("call started")
    else:
        print("call ended")
    try:
        ws = create_connection(CUBE_URL, timeout=5)
        ws.send(f"{TOKEN}:{command}")
        ws.close()
    except Exception as e:
        print(f"cube unreachable: {e}")


def monitor_mic():
    last_status = None
    while True:
        status = mic_in_use()
        if status != last_status:
            send_command(status)
            last_status = status
        wait_for_mic_change()
        time.sleep(0.5)


monitor_mic()
