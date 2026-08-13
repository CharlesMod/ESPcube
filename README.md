# ESPcube 🟥🟩

A physical **meeting indicator**: when any app on your computer opens the
microphone, the LED cube on your desk (or outside your office door) turns
**red**. When the mic closes, it turns **green**. No more family members
wandering into frame.

```
┌─────────────┐  mic state    ┌────────────┐   WebSocket    ┌──────────┐  IR (NEC)   ┌──────────┐
│ OS audio    │──(push        │  host app  │──"<token>:R"──▶│ ESP8266  │──────38kHz─▶│ LED cube │
│ stack       │   events)────▶│  (Python)  │                │ + IR LED │             │controller│
└─────────────┘               └────────────┘                └──────────┘             └──────────┘
```

The cube itself is any cheap RGB/WRGB LED lamp that ships with a 24-key NEC
IR remote — the ESP8266 simply impersonates the remote.

## Features

- **Event-driven mic detection on all three desktop OSes** — no screen
  scraping, no polling loops. The watcher thread sleeps until the OS says
  the mic state changed, then reacts in under a second:

  | OS      | Source of truth                                                | Wakeup mechanism                    |
  |---------|----------------------------------------------------------------|-------------------------------------|
  | Windows | `CapabilityAccessManager\ConsentStore\microphone` registry keys (the same data behind the taskbar mic dot) | `RegNotifyChangeKeyValue` |
  | macOS   | CoreAudio `kAudioDevicePropertyDeviceIsRunningSomewhere` on the default input device | `AudioObjectAddPropertyListener` |
  | Linux   | PulseAudio/PipeWire recording streams (`pactl list source-outputs`, monitor sources excluded) | `pactl subscribe` |

- **OTA updates** — after the first USB flash, reflash over WiFi forever.
- **Authenticated commands** — the cube ignores WebSocket messages without
  the shared token, so nobody else on your LAN can turn your office red.
- **Brightness ramping** — these controllers have no absolute-brightness
  code, so the firmware re-sends `BRIGHT_UP` after every color change.
- **`/info` diagnostics endpoint** — flash geometry, sketch size, core
  version. Check this first when OTA misbehaves.
- **`ircube.py` bench tool** — dependency-free REPL for firing individual
  IR codes and sweeping brightness while you watch the cube.

## Hardware

- ESP8266/ESP8285 board
- IR LED on GPIO4 (add a transistor driver for more range)
- Any RGB/WRGB LED cube using the common 24-key NEC remote

If your lamp uses different IR codes, point `IRrecvDumpV2` (from
IRremoteESP8266's examples) at your remote and swap the `#define`s at the
top of the sketch.

### Know your flash size before you flash

**This is the one that will cost you an afternoon.** Many of these modules
are **ESP8285** parts with 2 MB of *embedded* flash, not 4 MB NodeMCUs.
Build for the wrong size and the sketch still runs fine over USB — but
every OTA update is rejected with `ERROR[8]: Flash config wrong`, because
the updater compares the running image's flash header against the real
chip and needs room for two images.

Check the real chip before building:

```bash
esptool.py --port /dev/ttyUSB0 flash_id
```

Then match it in the IDE: **Board: "Generic ESP8266 Module", Flash Size:
"2MB"** (or whatever `flash_id` reported). Once running, `http://<cube>/info`
reports `flash ok: yes|NO` so you never have to guess again.

Rebuilding at the correct size does *not* fix a device already flashed
wrong — the check reads the header of the **running** firmware, so it takes
one corrected USB flash to escape.

### Serial flashing tips

- Use the stable by-id path, not `/dev/ttyUSB0`. If the adapter is
  re-plugged, the `ttyUSB0` node can point at a dead instance and every
  connection silently times out:
  `/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_XXXXXXXX-if00-port0`
- Don't run a separate `flash_id`/`chip_id` probe with `--after no_reset`
  before flashing. That leaves esptool's RAM stub loader running and breaks
  the next sync. Do the whole job in one invocation.
- Watch for **backpowering**: if the board is fed 5 V *and* the USB-serial
  adapter, unplugging only one won't actually reset it. Pull both.
- If the module is powered from an FT232R's 3V3 pin, expect brownouts —
  that regulator sources ~50 mA and an ESP8266 pulls ~350 mA bursts when
  the radio transmits.

## Firmware setup

1. Arduino IDE with the ESP8266 core, plus libraries: `ESPAsyncTCP`,
   `ESPAsyncWebServer`, `IRremoteESP8266`.
2. `cp firmware/ESPcube/secrets.h.example firmware/ESPcube/secrets.h` and
   fill in WiFi credentials, a command token, and an OTA password.
3. Set the board and flash size per the section above, then flash **over
   USB once**.
4. Every flash after that: Tools → Port → *Network ports* →
   `ESPcubeXXXX`. The IDE prompts for your OTA password.

The cube strobes while connecting to WiFi, flashes white on success, and
strobes again during an OTA update.

## Host setup

```bash
cd host
pip install -r requirements.txt   # websocket-client, pystray, pillow
cp config.example.py config.py    # set cube IP + the token from secrets.h
python ESPcube.py                 # tray app
```

`ESPcubeEXE.py` is the same watcher without the tray icon — handy for
verifying detection on a new machine (join a test call, watch for
`call started`) and as a PyInstaller target
(`pyinstaller --onefile ESPcubeEXE.py`).

Per-OS notes:

- **Windows**: works out of the box.
- **macOS**: no extra dependencies (CoreAudio via ctypes).
- **Linux**: needs `pactl` (present on any PulseAudio or PipeWire desktop).
  The tray icon wants an AppIndicator/ayatana host; on GNOME that's the
  AppIndicator extension, or just run the headless variant.

Start it with your session: Task Scheduler (Windows), a Login Item or
launchd agent (macOS), or a systemd user service / autostart entry (Linux).

## Bench tool

```bash
python3 host/ircube.py            # REPL
python3 host/ircube.py R          # one-shot
python3 host/ircube.py sweep R    # guided brightness sweep
```

`sweep` floors the brightness, then steps `BRIGHT_UP` one press at a time
with a pause, printing the press number — watch the cube and note where it
stops changing. That tells you the controller's real step count instead of
guessing. `raw <name|hex>` fires a single IR code with no `ON` wrapper and
no auto-ramp, for isolating what each code actually does.

### A note on red vs white brightness

On a **WRGB** cube, `W` drives a dedicated white emitter while `R` drives a
single red die, so red reads dimmer than white even at maximum brightness.
If red isn't bright enough for your room, that's a hardware ceiling, not a
firmware bug — use a bigger cube or a dedicated red source.

## Protocol

One-shot WebSocket messages to `ws://<cube>/ws`, formatted
`<token>:<command>`:

| Command | Effect |
|---|---|
| `R` `G` `B` `W` `V` `P` `Y` `LG` `O` `YO` | solid colors (auto `ON` + brightness ramp) |
| `FLASH` `STROBE` `FADE` `SMOOTH` | built-in effects |
| `ON` `OFF` `BRIGHT_UP` `BRIGHT_DOWN` | power and manual brightness |
| `RAW:<hex>` | one raw NEC frame, no wrapper (diagnostics) |
| `BUMP:<n>` | press `BRIGHT_UP` n times (diagnostics) |

Anything on your network that can open a WebSocket can drive the cube — the
meeting indicator is just one client.

## Security notes

- Commands require a shared token; OTA requires a password. Both live only
  in gitignored files (`firmware/ESPcube/secrets.h`, `host/config.py`).
- Traffic is plaintext on your LAN (an ESP8266 isn't going to TLS its way
  out of that); the threat model is "mischievous roommate", not
  nation-state.

## License

MIT
