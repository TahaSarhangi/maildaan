"""Single-email PDF/PNG export for MailDaan (no external dependencies).

Renders one archived message — subject, sender, recipients, date, folders and
body — to a paginated PDF or a single PNG image using GDI+ through ctypes.
The rendering runs entirely offline on Windows' own text stack: Uniscribe
shapes and bidi-orders Persian (RTL), English (LTR) and mixed paragraphs, so
no language needs special handling here and no fonts or libraries are added
to the application. The PDF embeds the exact rendered pages as images.

The Excel report builder (export.py) is intentionally untouched: this module
serves only the "export this one open message" action of the viewer.

Layout is computed in points (Letter portrait, 0.75in margins) and rendered
at 2x device pixels (144 dpi): 1pt == 2px. HTML-only messages are exported
through the same plain-text derivation the archive itself uses, so the
export never renders untrusted HTML.
"""

import ctypes
import datetime as dt
import os
import re
import struct
import zlib
from ctypes import wintypes

import config
import dialogs
import storage
from export import jalali_text, load_locale, readable_text
from imap_sync import html_to_text

# ---------------------------------------------------------------- geometry
SCALE = 2                  # device pixels per point (144 dpi render)
PAGE_W_PT = 612.0          # US Letter portrait
PAGE_H_PT = 792.0
MARGIN_PT = 54.0           # 0.75in
CONTENT_W_PT = PAGE_W_PT - 2 * MARGIN_PT
CONTENT_H_PT = PAGE_H_PT - 2 * MARGIN_PT


def _px(pt):
    return int(round(pt * SCALE))


PAGE_W, PAGE_H = _px(PAGE_W_PT), _px(PAGE_H_PT)
MARGIN = _px(MARGIN_PT)
CONTENT_W, CONTENT_H = _px(CONTENT_W_PT), _px(CONTENT_H_PT)

PNG_MAX_H = 16000          # single-image height safety cap (GDI+ limit is 32k)

# Font sizes in points; line factors keep the reading rhythm of the viewer.
SZ_SUBJECT, SZ_META, SZ_BODY, SZ_FOOTER = 15.0, 10.0, 11.0, 8.5
LH_BODY, LH_OTHER = 1.5, 1.35
RULE_H = 12                # vertical slot reserved for a separator rule

INK = 0xFF1E1E1E
MUTED = 0xFF6E6E6E
RULE = 0xFFD2D2D2
ACCENT = 0xFF1F4E79

FONT_FAMILY = "Segoe UI"   # system font with full Persian/Arabic shaping

# ------------------------------------------------------------------- GDI+
_gdi = ctypes.WinDLL("gdiplus.dll")

PixelFormat24bppRGB = 0x00021808
UnitPixel = 2
TextRenderingHintAntiAlias = 4
StringFormatFlagsDirectionRightToLeft = 0x00000001
StringFormatFlagsNoWrap = 0x00001000
FontStyleRegular = 0
FontStyleBold = 1


class _GdiplusStartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", wintypes.UINT),
                ("DebugEventCallback", ctypes.c_void_p),
                ("SuppressBackgroundThread", wintypes.BOOL),
                ("SuppressExternalCodecs", wintypes.BOOL)]


class _RectF(ctypes.Structure):
    _fields_ = [("X", ctypes.c_float), ("Y", ctypes.c_float),
                ("Width", ctypes.c_float), ("Height", ctypes.c_float)]


def _call(fn, *args):
    fn.restype = ctypes.c_int
    status = fn(*args)
    if status != 0:
        raise RuntimeError("GDI+ %s failed (status %d)" % (fn.__name__, status))
    return status


class _Gdi:
    """One GDI+ session: token, fonts, string formats and reusable brushes.

    Created per export call in the calling (bridge) thread and always shut
    down again; nothing here touches window handles. A tiny scratch bitmap
    backs text measurement, which GDI+ needs a graphics context for.
    """

    def __init__(self):
        token = ctypes.c_ulong(0)
        inp = _GdiplusStartupInput()
        inp.GdiplusVersion = 1
        _call(_gdi.GdiplusStartup, ctypes.byref(token), ctypes.byref(inp), None)
        self._token = token
        self._fonts = {}
        self._brushes = {}
        self._scratch_bmp = ctypes.c_void_p()

        def make_format(flags):
            handle = ctypes.c_void_p()
            _call(_gdi.GdipCreateStringFormat, 0, 0, ctypes.byref(handle))
            _call(_gdi.GdipSetStringFormatFlags, handle, flags)
            _call(_gdi.GdipSetStringFormatAlign, handle, 0)      # near
            _call(_gdi.GdipSetStringFormatLineAlign, handle, 0)  # near
            return handle

        self.fmt_measure = make_format(StringFormatFlagsNoWrap)
        self.fmt_ltr = make_format(StringFormatFlagsNoWrap)
        self.fmt_rtl = make_format(StringFormatFlagsNoWrap
                                   | StringFormatFlagsDirectionRightToLeft)
        gfx = ctypes.c_void_p()
        _call(_gdi.GdipCreateBitmapFromScan0, 4, 4, 12,
              PixelFormat24bppRGB, None, ctypes.byref(self._scratch_bmp))
        _call(_gdi.GdipGetImageGraphicsContext, self._scratch_bmp,
              ctypes.byref(gfx))
        self._measure_gfx = gfx

    def font(self, size_pt, bold=False):
        key = (size_pt, bold)
        if key not in self._fonts:
            family = ctypes.c_void_p()
            wide = (FONT_FAMILY + "\0").encode("utf-16-le")
            _call(_gdi.GdipCreateFontFamilyFromName, wide, None,
                  ctypes.byref(family))
            handle = ctypes.c_void_p()
            _call(_gdi.GdipCreateFont, family, ctypes.c_float(size_pt * SCALE),
                  FontStyleBold if bold else FontStyleRegular, UnitPixel,
                  ctypes.byref(handle))
            _gdi.GdipDeleteFontFamily(family)
            self._fonts[key] = handle
        return self._fonts[key]

    def brush(self, argb):
        if argb not in self._brushes:
            handle = ctypes.c_void_p()
            _call(_gdi.GdipCreateSolidFill, ctypes.c_uint(argb),
                  ctypes.byref(handle))
            self._brushes[argb] = handle
        return self._brushes[argb]

    def text_width(self, text, font):
        """Width of one unwrapped line, in device px."""
        wide = (text or "").encode("utf-16-le")
        layout = _RectF(0.0, 0.0, 100000.0, 10000.0)
        bounds = _RectF()
        _call(_gdi.GdipMeasureString, self._measure_gfx, wide, len(text or ""),
              font, ctypes.byref(layout), self.fmt_measure,
              ctypes.byref(bounds), None, None)
        return bounds.Width

    def draw_text(self, gfx, text, font, x, y, w, argb, rtl):
        if not text:
            return
        wide = text.encode("utf-16-le")
        layout = _RectF(ctypes.c_float(x), ctypes.c_float(y),
                        ctypes.c_float(w), ctypes.c_float(10000.0))
        _call(_gdi.GdipDrawString, gfx, wide, len(text), font,
              ctypes.byref(layout), self.fmt_rtl if rtl else self.fmt_ltr,
              self.brush(argb))

    def fill_rect(self, gfx, x, y, w, h, argb):
        _call(_gdi.GdipFillRectangle, gfx, self.brush(argb),
              ctypes.c_float(x), ctypes.c_float(y),
              ctypes.c_float(w), ctypes.c_float(h))

    def page_graphics(self, width, height):
        """White 24bpp RGB bitmap plus its graphics context and raw buffer."""
        stride = width * 3
        if stride % 4:
            stride += 4 - (stride % 4)     # GDI+ requires 32-bit aligned strides
        buf = ctypes.create_string_buffer(stride * height)
        ctypes.memset(buf, 255, stride * height)
        bmp = ctypes.c_void_p()
        _call(_gdi.GdipCreateBitmapFromScan0, width, height, stride,
              PixelFormat24bppRGB, buf, ctypes.byref(bmp))
        gfx = ctypes.c_void_p()
        _call(_gdi.GdipGetImageGraphicsContext, bmp, ctypes.byref(gfx))
        _call(_gdi.GdipSetTextRenderingHint, gfx, TextRenderingHintAntiAlias)
        return gfx, bmp, buf, stride

    def close(self):
        _gdi.GdipDeleteGraphics(self._measure_gfx)
        _gdi.GdipDisposeImage(self._scratch_bmp)
        for font in self._fonts.values():
            _gdi.GdipDeleteFont(font)
        for brush in self._brushes.values():
            _gdi.GdipDeleteBrush(brush)
        for fmt in (self.fmt_measure, self.fmt_ltr, self.fmt_rtl):
            _gdi.GdipDeleteStringFormat(fmt)
        _gdi.GdiplusShutdown(self._token)


# --------------------------------------------------------------- containers
def _png_bytes(rgb, width, height, stride):
    """Minimal PNG: 8-bit RGB, filter type 0 per scanline, zlib-compressed."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    raw = bytearray()
    for y in range(height):
        raw.append(0)      # PNG row filter: none
        raw += rgb[y * stride:y * stride + width * 3]
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 6)) +
            chunk(b"IEND", b""))


def _pdf_bytes(pages):
    """Minimal PDF: every rendered page becomes one full-page RGB image.

    Image streams are simply Flate-compressed, tightly packed RGB rows: the
    pixels GDI+ rendered are written exactly as they are, with no predictor
    and no per-row filter bytes. (An earlier version declared the PNG row
    predictor but wrote unfiltered data, so readers applied an inverse
    PNG-Sub transform to already-unfiltered rows and distorted every page.)
    The page geometry is plain Letter portrait, matching the bitmaps exactly.
    """
    count = len(pages)
    kids = b" ".join(b"%d 0 R" % (3 + index * 3) for index in range(count))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [ " + kids + b" ] /Count %d >>" % count,
    ]
    for index, (rgb, width, height, stride) in enumerate(pages):
        page_no = 3 + index * 3
        content_no = page_no + 1
        image_no = page_no + 2
        content = b"q %g 0 0 %g 0 0 cm /Im%d Do Q" % (PAGE_W_PT, PAGE_H_PT,
                                                      index)
        # Tightly packed, unfiltered RGB rows (any GDI+ stride padding
        # excluded): 3 bytes per pixel, width*3 per row, height rows.
        # FlateDecode only — no predictor, so readers render the exact bytes.
        img = bytearray()
        for y in range(height):
            img += rgb[y * stride:y * stride + width * 3]
        stream = zlib.compress(bytes(img), 6)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %g %g] "
            b"/Resources << /XObject << /Im%d %d 0 R >> >> "
            b"/Contents %d 0 R >>" % (PAGE_W_PT, PAGE_H_PT, index, image_no,
                                      content_no))
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content +
                       b"\nendstream")
        objects.append(
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 "
            b"/Filter /FlateDecode /Length %d >>\n"
            b"stream\n" % (width, height, len(stream)) +
            bytes(stream) + b"\nendstream")

    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % index + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref))
    return bytes(out)


# ------------------------------------------------------------------- layout
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TOKEN_RE = re.compile(r"\S+\s*|\s+")
_RTL_RANGES = ((0x0590, 0x05FF), (0x0600, 0x06FF), (0x0700, 0x074F),
               (0xFB1D, 0xFDFF), (0xFE70, 0xFEFF))


def _is_rtl(text):
    """Paragraph direction from the first strong (bidi-classified) character."""
    for char in text or "":
        code = ord(char)
        if any(lo <= code <= hi for lo, hi in _RTL_RANGES):
            return True
        if char.isalpha():
            return False
    return False


def _normalize_body(value):
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\t", "    ")
    return _CTRL_RE.sub("", text)


def _split_long_token(token, font, max_w, gdi):
    """Break a token wider than the content area (an unbroken URL) by
    characters so no line can overflow the page."""
    if gdi.text_width(token, font) <= max_w:
        return [token]
    pieces, acc = [], ""
    for char in token:
        if not acc or gdi.text_width(acc + char, font) <= max_w:
            acc += char
        else:
            pieces.append(acc)
            acc = char
    if acc:
        pieces.append(acc)
    return pieces


def _wrap(text, font, max_w, gdi):
    """Word-wrap one paragraph to display lines, preserving indentation."""
    lines = []
    for hard_line in text.split("\n"):
        current = ""
        for token in _TOKEN_RE.findall(hard_line):
            for piece in _split_long_token(token, font, max_w, gdi):
                if not current:
                    current = piece
                elif gdi.text_width(current + piece, font) <= max_w:
                    current += piece
                else:
                    lines.append(current.rstrip())
                    current = piece.lstrip() or " "
        lines.append(current.rstrip() if current else " ")
    return lines or [" "]


def _address(name, email):
    name = readable_text(name or "")
    email = (email or "").strip()
    if name and email:
        return name + " <" + email + ">"
    return name or email


def _build_rows(msg, lang):
    """Content model of the export: a list of drawing rows.

    A row is ("title"|"meta"|"body"|"footer", text, rtl) or ("rule",).
    Meta rows are already combined into a single "Label: value" line.
    """
    viewer = load_locale(lang).get("viewer", {})
    rows = []
    subject = readable_text(msg.get("subject")) or \
        viewer.get("noSubject", "(No subject)")
    rows.append(("title", subject, _is_rtl(subject)))

    def meta(label, value):
        text = readable_text(value)
        if text and text.strip():
            combined = str(label) + ": " + text
            rows.append(("meta", combined, _is_rtl(combined)))

    meta(viewer.get("from", "From"),
         _address(msg.get("sender_name"), msg.get("sender_email")))
    for kind in ("to", "cc", "bcc"):
        group = [r for r in msg.get("recipients") or []
                 if r.get("rtype") == kind]
        if not group:
            continue
        joined = "; ".join(_address(r.get("name"), r.get("email"))
                           for r in group)
        meta(viewer.get(kind, kind.upper()), joined)
    date_value = msg.get("date_utc") or ""
    meta(viewer.get("date", "Date"),
         jalali_text(date_value) if lang == "fa" else date_value[:16].strip())
    meta(viewer.get("folders", "Folders"), msg.get("folder_names"))
    rows.append(("rule",))

    body = _normalize_body(readable_text(msg.get("plain_body")))
    if not body.strip() and msg.get("html_body"):
        # HTML-only message: export its derived plain text, never its markup.
        body = _normalize_body(html_to_text(readable_text(msg["html_body"])))
    if body.strip():
        for paragraph in body.split("\n\n"):
            paragraph = paragraph.strip("\n")
            if not paragraph.strip():
                continue
            for line in paragraph.split("\n"):
                rows.append(("body", line.rstrip(), _is_rtl(line)))
    else:
        rows.append(("body", viewer.get("emptyBody", "(empty message)"), False))

    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stamp = jalali_text(now) if lang == "fa" else now[:16]
    footer = viewer.get("exportFooter",
                        "Exported with MailDaan {version} on {date}")
    rows.append(("footer",
                 footer.replace("{version}", config.APP_VERSION)
                       .replace("{date}", stamp), False))
    return rows


# ---------------------------------------------------------------- rendering
LINE_FACTOR = {"title": LH_OTHER, "meta": LH_OTHER, "body": LH_BODY,
               "footer": LH_OTHER}
SPACE_AFTER = {"title": 10, "meta": 3, "body": 0, "footer": 26}
FONT_PT = {"title": SZ_SUBJECT, "meta": SZ_META, "body": SZ_BODY,
           "footer": SZ_FOOTER}
COLOR = {"title": ACCENT, "meta": MUTED, "body": INK, "footer": MUTED}
BOLD = {"title": True, "meta": False, "body": False, "footer": False}


def _prepare(rows, gdi):
    """Wrap every text row into display lines with concrete heights.

    Returns a list of items: {"kind", "text", "rtl", "h"} where h is the
    vertical slot (line height plus spacing) in device pixels.
    """
    items = []
    for row in rows:
        if row[0] == "rule":
            items.append({"kind": "rule", "text": "", "rtl": False,
                          "h": RULE_H})
            continue
        kind, text, rtl = row[0], row[1], row[2]
        font = gdi.font(FONT_PT[kind], BOLD[kind])
        line_h = FONT_PT[kind] * LINE_FACTOR[kind] * SCALE
        for line in _wrap(text, font, CONTENT_W - 4, gdi):
            items.append({"kind": kind, "text": line, "rtl": rtl,
                          "h": line_h + SPACE_AFTER[kind]})
    return items


def _render_page(items, height, gdi):
    """Render one page's items onto a fresh white bitmap."""
    height = int(round(height))
    gfx, bmp, buf, stride = gdi.page_graphics(PAGE_W, height)
    try:
        y = MARGIN
        for item in items:
            if item["kind"] == "rule":
                gdi.fill_rect(gfx, MARGIN, y + RULE_H / 2.0, CONTENT_W, 1.0,
                              RULE)
            else:
                font = gdi.font(FONT_PT[item["kind"]], BOLD[item["kind"]])
                gdi.draw_text(gfx, item["text"], font, MARGIN, y, CONTENT_W,
                              COLOR[item["kind"]], item["rtl"])
            y += item["h"]
        return buf.raw, PAGE_W, height, stride
    finally:
        _gdi.GdipDeleteGraphics(gfx)
        _gdi.GdipDisposeImage(bmp)


def _render(msg, lang, fmt):
    """Render the message. Returns a list of (rgb, w, h, stride) page tuples:
    one PDF page per content page, or exactly one tall image for PNG."""
    gdi = _Gdi()
    try:
        items = _prepare(_build_rows(msg, lang), gdi)

        if fmt == "png":
            # One image for the whole message, with an honest cap: a message
            # too long for a single bitmap stops there and says so, because
            # the PDF export is the full-fidelity variant.
            budget = PNG_MAX_H - 2 * MARGIN
            footer = items[-1] if items and items[-1]["kind"] == "footer" \
                else None
            body_items = items[:-1] if footer else items
            kept, used = [], 0
            note = load_locale(lang).get("viewer", {}).get(
                "exportTruncated",
                "The rest of the message is not shown in the image export. "
                "Use the PDF export for the full message.")
            note_h = FONT_PT["meta"] * LINE_FACTOR["meta"] * SCALE + \
                SPACE_AFTER["meta"]
            allowance = budget - note_h - (footer["h"] if footer else 0)
            for item in body_items:
                if used + item["h"] > allowance:
                    break
                kept.append(item)
                used += item["h"]
            truncated = len(kept) < len(body_items)
            if truncated:
                kept.append({"kind": "meta", "text": note,
                             "rtl": _is_rtl(note), "h": note_h})
            if footer:
                kept.append(footer)
            height = 2 * MARGIN + int(round(sum(item["h"] for item in kept)))
            return [_render_page(kept, height, gdi)]

        # PDF: flow the items through as many Letter pages as needed. A
        # separator rule never starts a page (it would float at the margin).
        pages, current, used = [], [], 0
        for item in items:
            if current and used + item["h"] > CONTENT_H:
                pages.append(current)
                current, used = [], 0
                if item["kind"] == "rule":
                    continue
            if item["kind"] != "rule" or current:
                current.append(item)
                used += item["h"]
        if current:
            pages.append(current)
        return [_render_page(page, PAGE_H, gdi) for page in pages]
    finally:
        gdi.close()


# ------------------------------------------------------------------ façade
def render_to_file(message, lang, fmt, target):
    """Render the message and write the file (no dialog). Returns {"path"}
    on success or an error_key the frontend can report."""
    try:
        pages = _render(message, lang, fmt)
        blob = (_pdf_bytes(pages) if fmt == "pdf" else _png_bytes(*pages[0]))
        with open(target, "wb") as fh:
            fh.write(blob)
    except Exception as exc:                      # pragma: no cover
        return {"error_key": "common.saveFailed", "error_detail": str(exc)}
    return {"path": target}


def export_message(message, lang, fmt):
    """Export one message as 'pdf' or 'png' after a native Save As dialog.

    Mirrors the saveEml contract: None when the user cancels, {"path"} on
    success, or an error_key the frontend can report.
    """
    if fmt not in ("pdf", "png"):
        return {"error_key": "common.saveFailed"}
    viewer = load_locale(lang).get("viewer", {})
    subject = readable_text(message.get("subject")) or \
        viewer.get("noSubject", "message")
    date_part = (message.get("date_utc") or "")[:10] or "message"
    base = storage.safe_filename(subject, "message")[:80]
    default_name = "%s_%s.%s" % (base, date_part, fmt)
    target = dialogs.save_dialog("MailDaan", default_name, extension=fmt)
    if not target:
        return None
    return render_to_file(message, lang, fmt, target)
