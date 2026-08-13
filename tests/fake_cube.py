#!/usr/bin/env python3
"""A fake ESPcube for CI: accepts WebSocket connections, unmasks one text
frame per connection, and appends each received message to a file so the
test can assert exactly what the app sent.

    python fake_cube.py <port> <received-log-path>
"""

import base64
import hashlib
import socket
import sys
import threading

MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def handle(conn, out_path):
    try:
        with conn:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(1024)
                if not chunk:
                    return
                data += chunk
            key = None
            for line in data.decode("latin1").split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            accept = base64.b64encode(
                hashlib.sha1((key + MAGIC).encode()).digest()).decode()
            conn.sendall(
                ("HTTP/1.1 101 Switching Protocols\r\n"
                 "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                 f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())

            hdr = recv_exact(conn, 2)
            if not hdr:
                return
            length = hdr[1] & 0x7F      # cube commands are short frames
            mask = recv_exact(conn, 4)
            payload = bytearray(recv_exact(conn, length) or b"")
            for i in range(len(payload)):
                payload[i] ^= mask[i % 4]
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(payload.decode("utf-8", "replace") + "\n")
                f.flush()
    except Exception as e:
        print(f"handler error: {e!r}", flush=True)


def main():
    port, out_path = int(sys.argv[1]), sys.argv[2]
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(8)
    print(f"fake cube listening on 127.0.0.1:{port} -> {out_path}", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn, out_path),
                         daemon=True).start()


if __name__ == "__main__":
    main()
