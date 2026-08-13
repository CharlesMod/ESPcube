# Copy this file to config.py and fill in your own values.

# Leave as None to find the cube automatically (UDP broadcast, then mDNS).
# Set it only to pin a specific device, e.g. "ws://192.168.1.50/ws".
CUBE_URL = None

# Shared secret prepended to every command; must match wsToken in the
# firmware's secrets.h. Generate with: openssl rand -hex 6
TOKEN = "change-me"
