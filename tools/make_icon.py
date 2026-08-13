#!/usr/bin/env python3
"""Generate windows/meetmaster.ico with no third-party imaging library.

An .ico is just a small header plus embedded PNGs (Vista and later), and a
PNG is zlib-compressed scanlines — both are reachable from the standard
library, which keeps the build honest on a machine without Pillow.
"""

import pathlib
import struct
import zlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "windows" / "meetmaster.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)
FILL = (29, 157, 63)        # the same green the cube rests at
EDGE = (255, 255, 255)
SS = 4                      # supersampling factor for smooth corners


def rounded_rect_rgba(size):
    """Rasterize a rounded square, supersampled for anti-aliased edges."""
    big = size * SS
    pad = max(1, big // 10)
    radius = max(2, big // 5)
    edge_w = max(SS, big // 20)

    x0, y0, x1, y1 = pad, pad, big - pad - 1, big - pad - 1

    def inside(x, y, shrink=0):
        ax0, ay0 = x0 + shrink, y0 + shrink
        ax1, ay1 = x1 - shrink, y1 - shrink
        r = max(0, radius - shrink)
        if not (ax0 <= x <= ax1 and ay0 <= y <= ay1):
            return False
        # Corner circles
        for cx, cy in ((ax0 + r, ay0 + r), (ax1 - r, ay0 + r),
                       (ax0 + r, ay1 - r), (ax1 - r, ay1 - r)):
            if ((x < ax0 + r and y < ay0 + r) or (x > ax1 - r and y < ay0 + r) or
                    (x < ax0 + r and y > ay1 - r) or (x > ax1 - r and y > ay1 - r)):
                if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
                    # only reject against the nearest corner
                    near = min(((cx2, cy2) for cx2, cy2 in
                                ((ax0 + r, ay0 + r), (ax1 - r, ay0 + r),
                                 (ax0 + r, ay1 - r), (ax1 - r, ay1 - r))),
                               key=lambda c: (x - c[0]) ** 2 + (y - c[1]) ** 2)
                    if (x - near[0]) ** 2 + (y - near[1]) ** 2 > r * r:
                        return False
                break
        return True

    # Accumulate supersamples down to the final resolution.
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            fill_hits = edge_hits = 0
            for sy in range(SS):
                for sx in range(SS):
                    x, y = px * SS + sx, py * SS + sy
                    if inside(x, y):
                        fill_hits += 1
                        if not inside(x, y, edge_w):
                            edge_hits += 1
            total = SS * SS
            if not fill_hits:
                row += bytes((0, 0, 0, 0))
                continue
            alpha = int(255 * fill_hits / total)
            edge_frac = edge_hits / fill_hits
            colour = tuple(
                int(FILL[i] * (1 - edge_frac * 0.55) + EDGE[i] * edge_frac * 0.55)
                for i in range(3))
            row += bytes((*colour, alpha))
        rows.append(bytes(row))
    return rows


def png_bytes(rows, size):
    raw = b"".join(b"\x00" + r for r in rows)     # filter type 0 per scanline

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body +
                struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def main():
    images = [(s, png_bytes(rounded_rect_rgba(s), s)) for s in SIZES]

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        dim = 0 if size == 256 else size          # 0 encodes 256 in an ICO
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                               len(data), offset)
        offset += len(data)
        blobs += data

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(header + entries + blobs)
    print(f"wrote {OUT.relative_to(OUT.parent.parent)} "
          f"({len(header + entries + blobs):,} bytes, sizes {list(SIZES)})")


if __name__ == "__main__":
    main()
