"""ESPcube headless/console variant: same watcher, no tray icon.

Useful for testing the detection backend on a new machine, and as the
PyInstaller build target.
"""

import time

from websocket import create_connection

from config import CUBE_URL, TOKEN
from discover import find_cube
from mic_detect import mic_in_use, wait_for_mic_change

cube_url = find_cube(explicit=CUBE_URL)
print("Watching the mic (event-driven, no polling)...")


def send_command(mic_active):
    global cube_url
    command = 'R' if mic_active else 'G'
    print("call started" if command == 'R' else "call ended")

    # Two attempts: a moved cube (DHCP, router reboot) fails once, then
    # rediscovery finds it again without restarting the app.
    for attempt in (1, 2):
        try:
            ws = create_connection(cube_url, timeout=5)
            ws.send(f"{TOKEN}:{command}")
            ws.close()
            return
        except Exception as e:
            if attempt == 1:
                try:
                    cube_url = find_cube(verbose=False, explicit=CUBE_URL)
                    continue
                except SystemExit:
                    pass
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
