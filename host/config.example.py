# Copy this file to config.py and fill in your own values.

# WebSocket endpoint of the cube (its IP on your LAN, or ESPcubeXXXX.local
# if mDNS resolves on your network).
CUBE_URL = "ws://192.168.1.123/ws"

# Shared secret prepended to every command; must match wsToken in the
# firmware's secrets.h. Generate with: openssl rand -hex 6
TOKEN = "change-me"
