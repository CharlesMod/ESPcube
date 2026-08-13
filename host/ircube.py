#!/usr/bin/env python3
"""ircube — interactive bench tool for poking the cube's IR controller.

Dependency-free (raw WebSocket over sockets), so it runs anywhere Python
does without a pip install.

    python3 ircube.py                 # REPL
    python3 ircube.py R               # one-shot named command
    python3 ircube.py sweep R         # guided brightness sweep on red

REPL commands:
    R G B W ...     firmware's normal path (ON + color + auto-ramp)
    raw R           send just the red code, no ON, no ramp
    raw ff906f      send an arbitrary NEC code
    bump 5          press BRIGHT_UP 5 times
    down 5          press BRIGHT_DOWN 5 times
    sweep R         step brightness one press at a time, pausing to watch
    keys            list the known 24-key codes
    quit
"""

import base64
import os
import socket
import struct
import sys
import time

try:
    from config import CUBE_URL, TOKEN
except ImportError:
    raise SystemExit("no config.py — copy config.example.py and fill it in")

# Standard 24-key RGB(W) remote, NEC. Row order matches the physical remote.
KEYS = {
    "BRIGHT_UP": 0xFFA05F, "BRIGHT_DOWN": 0xFF20DF, "OFF": 0xFF609F, "ON": 0xFFE01F,
    "R":  0xFF906F, "G":  0xFF10EF, "B":  0xFF50AF, "W": 0xFFD02F,
    "R1": 0xFFB04F, "G1": 0xFF30CF, "B1": 0xFF708F, "FLASH":  0xFFF00F,
    "R2": 0xFFA857, "G2": 0xFF28D7, "B2": 0xFF6897, "STROBE": 0xFFE817,
    "R3": 0xFF9867, "G3": 0xFF18E7, "B3": 0xFF58A7, "FADE":   0xFFD827,
    "R4": 0xFF8877, "G4": 0xFF08F7, "B4": 0xFF48B7, "SMOOTH": 0xFFC837,
}


def _parse_url(url):
    rest = url.split("://", 1)[-1]
    hostport, _, path = rest.partition("/")
    host, _, port = hostport.partition(":")
    return host, int(port or 80), "/" + path


HOST, PORT, PATH = _parse_url(CUBE_URL)


def send(payload):
    """Open a WebSocket, send one text frame, close."""
    key = base64.b64encode(os.urandom(16)).decode()
    req = (f"GET {PATH} HTTP/1.1\r\nHost: {HOST}\r\nUpgrade: websocket\r\n"
           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
           f"Sec-WebSocket-Version: 13\r\n\r\n")
    msg = f"{TOKEN}:{payload}".encode()
    try:
        s = socket.create_connection((HOST, PORT), timeout=8)
    except OSError as e:
        print(f"  ! cube unreachable: {e}")
        return False
    with s:
        s.sendall(req.encode())
        if b"101" not in s.recv(1024).split(b"\r\n")[0]:
            print("  ! handshake refused")
            return False
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(msg))
        s.sendall(b"\x81" + bytes([0x80 | len(msg)]) + mask + masked)
    print(f"  -> {payload}")
    return True


def raw(name_or_hex):
    key = name_or_hex.upper()
    code = KEYS.get(key)
    if code is None:
        code = int(name_or_hex, 16)
    send(f"RAW:{code:06X}")


def sweep(color="R", steps=24, pause=1.2):
    """Set a color at minimum, then walk brightness up one press at a time.

    Watch the cube and note the press number where it stops getting
    brighter — that's the controller's real step count.
    """
    print(f"\nSweeping {color}: dropping to minimum first...")
    raw("ON")
    time.sleep(0.4)
    raw(color)
    time.sleep(0.4)
    send("BUMP:0")
    for _ in range(30):  # floor it
        send(f"RAW:{KEYS['BRIGHT_DOWN']:06X}")
        time.sleep(0.12)
    print("at minimum. stepping up — watch the cube:\n")
    for i in range(1, steps + 1):
        send(f"RAW:{KEYS['BRIGHT_UP']:06X}")
        print(f"    press {i:2d}")
        time.sleep(pause)
    print("\nsweep done. Which press number did it stop brightening at?")


def repl():
    print(f"ircube -> {HOST}:{PORT}{PATH}   ('help' for commands, 'quit' to exit)")
    while True:
        try:
            line = input("cube> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]
        if cmd in ("quit", "exit", "q"):
            return
        elif cmd == "help":
            print(__doc__)
        elif cmd == "keys":
            for k, v in KEYS.items():
                print(f"    {k:<12} 0x{v:06X}")
        elif cmd == "raw":
            raw(args[0]) if args else print("  usage: raw <name|hex>")
        elif cmd == "bump":
            send(f"BUMP:{args[0] if args else 1}")
        elif cmd == "down":
            for _ in range(int(args[0]) if args else 1):
                send(f"RAW:{KEYS['BRIGHT_DOWN']:06X}")
                time.sleep(0.12)
        elif cmd == "sweep":
            sweep(args[0].upper() if args else "R")
        else:
            send(parts[0].upper())


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "sweep":
        sweep(sys.argv[2].upper())
    elif len(sys.argv) > 1:
        send(sys.argv[1].upper())
    else:
        repl()
