"""Regenerate assets/maildaan.ico from the MailDaan vector design.

Renders the maildaan.svg geometry (envelope archiving into a storage tray)
at every Windows icon size with 4x supersampling, then assembles a
multi-resolution .ico. Run from the repository root:

    python tools/make_icon.py
"""

import io
import os
import struct

from PIL import Image, ImageDraw

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SS = 4  # supersampling factor

BODY = (31, 78, 121)        # #1F4E79 envelope
TRAY = (46, 109, 164)       # #2E6DA4 storage tray
FLAP = (242, 201, 76)       # #F2C94C flap + slot
TRANSPARENT = (0, 0, 0, 0)


def _scale(v, size):
    return v * size / 256.0


def render(size):
    """Draw the icon geometry at `size` (256-unit coordinate space)."""
    canvas = size * SS
    img = Image.new("RGBA", (canvas, canvas), TRANSPARENT)
    d = ImageDraw.Draw(img)

    def s(v):
        return _scale(v, canvas)

    # Envelope body: rounded rect (20,36) 216x140, radius 22
    d.rounded_rectangle([s(20), s(36), s(236), s(176)],
                        radius=s(22), fill=BODY + (255,))
    # Flap: polyline (20,52)-(128,140)-(236,52), round caps/joins, width 16
    a = (s(20), s(52))
    b = (s(128), s(140))
    c = (s(236), s(52))
    # Slightly thicker flap below 32px so the envelope stays legible at
    # taskbar/16px sizes without changing the design at larger sizes.
    stroke = 20 if size <= 24 else 16
    w = s(stroke)
    r = w / 2.0
    d.line([a, b, c], fill=FLAP + (255,), width=max(1, round(w)),
           joint="curve")
    for pt in (a, c):  # round end caps
        d.ellipse([pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r],
                  fill=FLAP + (255,))
    # Storage tray: rounded rect (44,192) 168x52, radius 14
    d.rounded_rectangle([s(44), s(192), s(212), s(244)],
                        radius=s(14), fill=TRAY + (255,))
    # Slot: rounded rect (102,206) 52x12, radius 6
    d.rounded_rectangle([s(102), s(206), s(154), s(218)],
                        radius=s(6), fill=FLAP + (255,))

    return img.resize((size, size), Image.LANCZOS)


def assemble(images, path):
    """Write a multi-resolution .ico with PNG-encoded entries."""
    entries = []
    for size, img in images.items():
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        entries.append((size, buf.getvalue()))

    count = len(entries)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    directory = b""
    blobs = b""
    for size, blob in entries:
        # ICONDIRENTRY: wPlanes=0, wBitCount=32 (the convention Pillow and
        # the Windows shell agree on; the planes field must not carry the
        # bit depth).
        directory += struct.pack("<BBBBHHII", size & 0xFF, size & 0xFF, 0, 0,
                                 0, 32, len(blob), offset + len(blobs))
        blobs += blob
    with open(path, "wb") as fh:
        fh.write(header + directory + blobs)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "..", "assets", "maildaan.ico")
    images = {size: render(size) for size in SIZES}
    assemble(images, os.path.normpath(out))
    print("wrote", os.path.normpath(out), "sizes:", ", ".join(map(str, SIZES)))


if __name__ == "__main__":
    main()
