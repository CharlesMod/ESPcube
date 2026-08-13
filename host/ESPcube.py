"""ESPcube tray app: watches the microphone, drives the cube red/green."""

import time

from pystray import Icon, MenuItem, Menu
from PIL import Image

from config import CUBE_URL, TOKEN
from wsclient import send_text
from discover import find_cube
from mic_detect import mic_in_use, wait_for_mic_change

cube_url = find_cube(explicit=CUBE_URL)
print("Watching the mic (event-driven, no polling)...")


def send_command(mic_active):
    global cube_url
    command = 'R' if mic_active else 'G'
    print("call started" if command == 'R' else "call ended")

    # Two attempts: if the cube moved (DHCP, router reboot), the cached
    # address fails once and rediscovery finds it again without a restart.
    for attempt in (1, 2):
        try:
            send_text(cube_url, f"{TOKEN}:{command}")
            return
        except Exception as e:
            if attempt == 1:
                try:
                    cube_url = find_cube(verbose=False, explicit=CUBE_URL)
                    continue
                except SystemExit:
                    pass
            # Cube offline / rebooting (e.g. mid-OTA) — keep watching and
            # retry on the next state change.
            print(f"cube unreachable: {e}")


def monitor_mic(icon):
    last_status = None
    while True:
        status = mic_in_use()
        if status != last_status:
            send_command(status)
            last_status = status
        wait_for_mic_change()
        time.sleep(0.5)  # let the OS state settle before rescanning


def setup(icon):
    icon.visible = True
    monitor_mic(icon)


icon = Icon('MicMonitor', icon=Image.open('cube.png'), menu=Menu(
    MenuItem('Exit', lambda icon: icon.stop())))
icon.run(setup)
