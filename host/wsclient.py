"""A one-frame WebSocket client in stdlib only.

The cube takes a single short text frame per command and closes, so the
full websocket-client dependency buys nothing here. Keeping this in the
standard library means the watcher runs on a locked-down work machine with
no pip install at all.
"""

import base64
import os
import socket


def parse_url(url):
    """ws://host[:port]/path -> (host, port, path)"""
    rest = url.split("://", 1)[-1]
    hostport, _, path = rest.partition("/")
    host, _, port = hostport.partition(":")
    return host, int(port or 80), "/" + path


def send_text(url, message, timeout=5):
    """Open, send one masked text frame, close. Raises OSError on failure."""
    host, port, path = parse_url(url)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (f"GET {path} HTTP/1.1\r\n"
           f"Host: {host}\r\n"
           f"Upgrade: websocket\r\n"
           f"Connection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\n"
           f"Sec-WebSocket-Version: 13\r\n\r\n")

    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(req.encode())
        resp = s.recv(1024)
        if b"101" not in resp.split(b"\r\n")[0]:
            raise OSError(f"handshake refused: {resp.split(chr(13).encode())[0]!r}")

        payload = message.encode()
        if len(payload) > 125:
            raise ValueError("command too long for a single short frame")
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        # FIN + text opcode, MASK bit + length, mask key, masked payload
        s.sendall(b"\x81" + bytes([0x80 | len(payload)]) + mask + masked)
