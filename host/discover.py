"""Find the cube on the LAN without anybody hardcoding an IP address.

Four strategies, cheapest first:

  1. an explicit CUBE_URL in config.py, if you set one (always wins)
  2. the address that worked last time, cached on disk
  3. a UDP broadcast the cube answers with its own address
  4. mDNS resolution of espcube.local

DHCP can move the cube whenever it likes and this still finds it, so the
only thing you configure is the shared token.
"""

import json
import pathlib
import socket
import struct
import sys
import time

DISCOVERY_PORT = 9999
MAGIC = b"ESPCUBE_DISCOVER"
MDNS_NAME = "espcube.local"
CACHE = pathlib.Path(__file__).with_name(".cube_cache.json")


def _broadcast_addresses():
    """Every plausible broadcast target, including per-interface ones.

    The global 255.255.255.255 is dropped by some routers and by Windows
    when several interfaces are up, so derive per-interface broadcasts too.
    """
    addrs = ["255.255.255.255"]
    try:  # POSIX: enumerate real interface broadcast addresses
        import fcntl
        import array
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        names = array.array("B", b"\0" * 4096)
        outbytes = struct.unpack("iL", fcntl.ioctl(
            s.fileno(), 0x8912,  # SIOCGIFCONF
            struct.pack("iL", 4096, names.buffer_info()[0])))[0]
        for i in range(0, outbytes, 40):
            name = bytes(names[i:i + 16]).split(b"\0", 1)[0].decode()
            if not name:
                continue
            try:
                brd = fcntl.ioctl(s.fileno(), 0x8919,  # SIOCGIFBRDADDR
                                  struct.pack("256s", name.encode()[:15]))
                addrs.append(socket.inet_ntoa(brd[20:24]))
            except OSError:
                pass
        s.close()
    except Exception:
        pass
    # Fallback: derive a /24 broadcast from our primary address.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        addrs.append(ip.rsplit(".", 1)[0] + ".255")
    except OSError:
        pass
    return list(dict.fromkeys(a for a in addrs if a != "0.0.0.0"))


def broadcast_discover(timeout=2.5):
    """Ask the cube to identify itself. Returns an IP string or None."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.4)
    try:
        s.bind(("", 0))
        for attempt in range(3):
            for addr in _broadcast_addresses():
                try:
                    s.sendto(MAGIC, (addr, DISCOVERY_PORT))
                except OSError:
                    pass
            deadline = time.time() + timeout / 3
            while time.time() < deadline:
                try:
                    data, addr = s.recvfrom(256)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if data.startswith(b"ESPCUBE:"):
                    parts = data.decode("utf-8", "replace").split(":")
                    return parts[1] if len(parts) > 1 else addr[0]
    finally:
        s.close()
    return None


def mdns_lookup():
    """espcube.local, resolvable wherever Avahi/Bonjour is present."""
    try:
        return socket.gethostbyname(MDNS_NAME)
    except OSError:
        return None


def _reachable(ip, timeout=1.0):
    try:
        with socket.create_connection((ip, 80), timeout=timeout):
            return True
    except OSError:
        return False


def _cached():
    try:
        return json.loads(CACHE.read_text()).get("ip")
    except Exception:
        return None


def _remember(ip):
    try:
        CACHE.write_text(json.dumps({"ip": ip}))
    except OSError:
        pass


def find_cube(verbose=True, explicit=None):
    """Return a ws:// URL for the cube, or raise SystemExit with advice."""
    def say(msg):
        if verbose:
            print(msg, file=sys.stderr)

    if explicit:
        return explicit

    ip = _cached()
    if ip and _reachable(ip):
        say(f"cube: {ip} (cached)")
        return f"ws://{ip}/ws"

    ip = broadcast_discover()
    if ip:
        say(f"cube: {ip} (broadcast)")
        _remember(ip)
        return f"ws://{ip}/ws"

    ip = mdns_lookup()
    if ip:
        say(f"cube: {ip} (mDNS {MDNS_NAME})")
        _remember(ip)
        return f"ws://{ip}/ws"

    raise SystemExit(
        "Could not find the cube on this network.\n"
        "  - is it powered and joined to WiFi? (it flashes white on join)\n"
        "  - is this machine on the same LAN/VLAN?\n"
        "  - some APs block client-to-client traffic; check AP isolation\n"
        "  - or set CUBE_URL explicitly in config.py")


if __name__ == "__main__":
    print(find_cube())
