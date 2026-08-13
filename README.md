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

**On Windows, grab
[MeetMaster.exe](https://github.com/CharlesMod/ESPcube/releases/latest)**
and double-click it. Everything else below is for building the cube or
running the watcher on macOS/Linux.

## Features

- **Event-driven mic detection on all three desktop OSes** — no screen
  scraping, no polling loops. The watcher thread sleeps until the OS says
  the mic state changed, then reacts in under a second:

  | OS      | Source of truth                                                | Wakeup mechanism                    |
  |---------|----------------------------------------------------------------|-------------------------------------|
  | Windows | `CapabilityAccessManager\ConsentStore\microphone` registry keys (the same data behind the taskbar mic dot) | `RegNotifyChangeKeyValue` |
  | macOS   | CoreAudio `kAudioDevicePropertyDeviceIsRunningSomewhere` on the default input device | `AudioObjectAddPropertyListener` |
  | Linux   | PulseAudio/PipeWire recording streams (`pactl list source-outputs`, monitor sources excluded) | `pactl subscribe` |

- **Zero configuration** — no IP addresses anywhere. The cube answers a UDP
  broadcast and advertises `espcube.local`; the host rediscovers it
  mid-run when DHCP moves it.
- **Day/night dimming** — full brightness by day, dimmer after dark,
  gliding over ~15 minutes at sunrise and sunset. Sun times are computed
  on-device from lat/lon, so no weather API is involved.
- **OTA updates** — after the first USB flash, reflash over WiFi forever.
- **Authenticated commands** — the cube ignores WebSocket messages without
  the shared token, so nobody else on your LAN can turn your office red.
- **Control panel served by the cube** — browse to it from any phone or
  laptop; no token is baked into the image.
- **`/info` diagnostics endpoint** — flash geometry, clock, sun times, and
  day/night state. Check this first when OTA misbehaves.
- **`ircube.py` bench tool** — dependency-free REPL for firing individual
  IR codes, sweeping brightness, and scanning the NEC command space.

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

## MeetMaster (Windows)

A single self-installing tray app, for the machine you actually take calls
on. Download `MeetMaster.exe` from the
[latest build](https://github.com/CharlesMod/ESPcube/actions/workflows/build-windows.yml)
(Artifacts section) and double-click it.

On first run it:

1. copies itself to `%LOCALAPPDATA%\Programs\MeetMaster\` — so it keeps
   working after you pull the USB stick out
2. registers auto-start for your user (`HKCU\...\CurrentVersion\Run`)
3. asks once for the cube's token
4. drops into the system tray and starts watching

**Right-click the tray icon** for:

| Item | Does |
|---|---|
| *(status line)* | shows free / on a call, and which cube it found |
| Start with Windows | checkable — toggles auto-start on the spot |
| Set token… | change the shared secret |
| Find cube again | force rediscovery after a router reboot |
| Exit | quit |

The icon itself is the status: **green** free, **red** on a call, **grey**
if the cube can't be reached. It finds the cube by broadcast, so no IP is
ever typed in.

Windows will warn that the exe is unsigned the first time — *More info →
Run anyway*. Code signing needs a certificate; there isn't one here. Some
antivirus engines also flag single-file PyInstaller builds on sight, which
is a known false positive for the packer rather than anything about this
program.

To uninstall: Exit from the tray menu, untick Start with Windows first (or
delete the `MeetMaster` value under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`), then delete
`%LOCALAPPDATA%\Programs\MeetMaster\` and `%APPDATA%\MeetMaster\`.

### Building it yourself

PyInstaller can't cross-compile, so the exe is built by
`.github/workflows/build-windows.yml` on a Windows runner. On a Windows box:

```bash
pip install pyinstaller pystray pillow
pyinstaller --onefile --windowed --name MeetMaster --paths host \
    --hidden-import pystray._win32 --icon windows/meetmaster.ico \
    windows/meetmaster.py
```

## Host setup (macOS / Linux, or Windows from source)

```bash
cd host
cp config.example.py config.py    # set TOKEN; leave CUBE_URL = None
python3 ESPcubeEXE.py             # console watcher — no dependencies at all
```

`ESPcubeEXE.py` is **pure standard library**: detection, discovery, and the
WebSocket frame are all hand-rolled, so it runs on a locked-down machine
with no pip install and no admin rights. It's also the quickest way to
verify a new box — join a test call and watch for `call started`.

The tray version needs two packages:

```bash
pip install -r requirements.txt   # pystray, pillow
python3 ESPcube.py
```

Per-OS notes:

- **Windows**: use [MeetMaster](#meetmaster-windows) instead — it packages
  all of this with auto-start and a tray menu.
- **macOS**: no extra dependencies (CoreAudio via ctypes).
- **Linux**: needs `pactl` (present on any PulseAudio or PipeWire desktop).
  The tray icon wants an AppIndicator/ayatana host; on GNOME that's the
  AppIndicator extension, or just run the headless variant.

To start it with your session: a Login Item or launchd agent (macOS), or a
systemd user service / autostart entry (Linux).

## Controller app

**Just browse to the cube** — `http://<cube-ip>/` serves the control panel
straight from the firmware. Colors, brightness, effects, and a raw-IR box,
on desktop or phone.

Enter the token once; the browser keeps it in localStorage. It is
deliberately **not** baked into the firmware image, so serving the page to
your LAN doesn't hand out the ability to drive the cube. The host field
prefills itself when the cube serves the page.

The same file lives at `host/cube_control.html` if you'd rather open it
from disk. It's the single source of truth — after editing it, run
`python3 tools/embed_html.py` to regenerate `firmware/ESPcube/webpage.h`,
then reflash.

## What the colors mean

| Cube | State |
|---|---|
| Pulsing blue | Busy — booting, joining WiFi, or taking an OTA update |
| Solid white | WiFi joined successfully (3 s) |
| Green | Mic closed — nobody's on a call. **This is the resting default.** |
| Red | Mic is open — you're on a call |

Red is never used for anything but an open mic, so it can't be
misread. The cube settles on green after boot rather than going dark,
because "no call in progress" is a real state worth showing.

The cube can't be driven while the ESP sits in the *serial* bootloader —
no code is running — so it holds whatever color it had.

## No hardcoded addresses

Neither end needs to know the other's IP. The host app finds the cube by,
in order:

1. an explicit `CUBE_URL` in `config.py`, if you set one (leave it `None`)
2. the address that worked last time, cached on disk
3. **UDP broadcast** — the cube listens on port 9999 and answers with its
   own address
4. **mDNS** — `espcube.local`

DHCP can move the cube whenever it likes; the first send fails, the app
rediscovers, and it keeps working without a restart. The control page has
the same property from the other direction: served by the cube, it reads
its own address out of the URL.

If discovery ever comes up empty, the usual cause is **AP client isolation**
(common on guest networks), which blocks the broadcast. Pinning `CUBE_URL`
works around it.

## Day/night brightness

The cube runs at full brightness during the day and backs off after dark,
gliding one step at a time over about 15 minutes so sunset reads as dusk
rather than a switch being thrown. Whatever color is showing at the time
just gets dimmer — the color itself never changes.

Sunrise and sunset are computed on the device from your latitude and
longitude (set in `secrets.h`) using the standard Almanac algorithm, so
there's no weather API to depend on — only NTP for the clock. Daylight
saving is handled by the POSIX `TZ` string, which encodes the rules.

Check what it thinks with `http://<cube>/info`:

```
local time: 12:38
sunrise: 05:56
sunset: 19:54
mode: day (full)
```

Tune the night level with `kNightDimSteps` in the sketch (how many steps
below maximum) and `kGlideStepMs` (how long the fade takes).

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

### Hunting for undocumented codes

This remote is NEC address `0x00`, so every possible button is
`00FF<cmd><~cmd>` — the whole command space is just 256 codes, and the 24
printed on the remote are only a tenth of them. `scan` walks all of them
with a pause between each, labelling the ones you already know:

```bash
python3 host/ircube.py            # then: scan       (all 256, ~6 min)
                                  #   or: scan 00 3f (a subrange)
```

Watch the cube and note any command that does something the remote's own
buttons can't — a direct brightness level, a different white balance, a
stored scene. Send one by number afterwards with `nec 90`.

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
