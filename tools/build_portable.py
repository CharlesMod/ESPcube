#!/usr/bin/env python3
"""Assemble MeetMaster-portable.zip — the no-admin, no-PyInstaller bundle.

Nothing is compiled, so this runs anywhere: it downloads the official
Windows embeddable Python runtime (signed python.exe, which endpoint
policies that block unsigned packed executables generally allow), lays the
stdlib-only app next to it, and zips the lot.

    python3 tools/build_portable.py [--no-download]

--no-download skips bundling the runtime, producing a small zip for
machines that already have Python installed.
"""

import argparse
import hashlib
import io
import pathlib
import shutil
import sys
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "MeetMaster-portable.zip"

# Last 3.13.x with published Windows binaries at the time of writing; the
# embeddable zip is ~11 MB and includes ctypes/winreg/socket — everything
# the app touches.
PY_VERSION = "3.13.1"
PY_URL = (f"https://www.python.org/ftp/python/{PY_VERSION}/"
          f"python-{PY_VERSION}-embed-amd64.zip")

APP_FILES = [
    (ROOT / "windows" / "portable" / "meetmaster_tray.py", "app/meetmaster_tray.py"),
    (ROOT / "host" / "mic_detect.py", "app/mic_detect.py"),
    (ROOT / "host" / "discover.py", "app/discover.py"),
    (ROOT / "host" / "wsclient.py", "app/wsclient.py"),
    (ROOT / "windows" / "meetmaster.ico", "app/meetmaster.ico"),
    (ROOT / "windows" / "meetmaster_red.ico", "app/meetmaster_red.ico"),
    (ROOT / "windows" / "meetmaster_grey.ico", "app/meetmaster_grey.ico"),
    (ROOT / "windows" / "portable" / "Install MeetMaster.bat", "Install MeetMaster.bat"),
    (ROOT / "windows" / "portable" / "Run without installing.bat", "Run without installing.bat"),
    (ROOT / "windows" / "portable" / "Uninstall MeetMaster.bat", "Uninstall MeetMaster.bat"),
    (ROOT / "windows" / "portable" / "README.txt", "README.txt"),
]


def fetch_runtime():
    print(f"downloading {PY_URL} ...")
    with urllib.request.urlopen(PY_URL, timeout=120) as r:
        data = r.read()
    print(f"  {len(data):,} bytes, sha256 {hashlib.sha256(data).hexdigest()[:16]}…")
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-download", action="store_true",
                    help="skip bundling the Python runtime")
    args = ap.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for src, arc in APP_FILES:
            if not src.exists():
                sys.exit(f"missing {src}")
            z.write(src, f"MeetMaster/{arc}")

        if not args.no_download:
            runtime = fetch_runtime()
            with zipfile.ZipFile(io.BytesIO(runtime)) as pz:
                for info in pz.infolist():
                    data = pz.read(info)
                    name = info.filename
                    # The ._pth controls sys.path in the embeddable build;
                    # add the app dir so sibling imports resolve.
                    if name.endswith("._pth"):
                        text = data.decode()
                        if "../app" not in text:
                            data = (text.rstrip() + "\n../app\n").encode()
                    z.writestr(f"MeetMaster/python/{name}", data)

    size = OUT.stat().st_size
    print(f"wrote {OUT.relative_to(ROOT)} ({size / 1e6:.1f} MB)")
    with zipfile.ZipFile(OUT) as z:
        bad = z.testzip()
        names = z.namelist()
    print(f"zip integrity: {'FAILED at ' + bad if bad else 'ok'}, "
          f"{len(names)} entries")


if __name__ == "__main__":
    main()
