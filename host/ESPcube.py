"""ESPcube tray app: watches the microphone, drives the cube red/green."""

import time

from websocket import create_connection
from pystray import Icon, MenuItem, Menu
from PIL import Image

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
        # Cube offline / rebooting (e.g. mid-OTA) — keep watching, retry on
        # the next state change
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
