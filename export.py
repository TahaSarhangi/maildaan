"""Bilingual professional Excel report builder for MailDaan (openpyxl only).

A report is always generated from an explicitly configured scope: selected
account, date range, folders, direction, drafts handling and optional
keyword. Every worksheet is derived from that same filtered message set, so
contact and domain statistics can never appear beside message counts that
were computed from a different scope.

The archived source data is never modified: MIME quirks of older archives
(quoted-printable payloads, RFC 2047 encoded-words) are repaired for display
only, at export time.

Report options (built by the "Create Report" dialog):

  {
    "purpose":  "comprehensive" | "activity" | "contacts" | "domains" | "messages",
    "scope":    {"range": "all"|"30d"|"year"|"custom",
                 "date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD",
                 "folder_ids": [int], "direction": "both"|"incoming"|"outgoing",
                 "include_drafts": bool, "keyword": ""},
    "content":  {"summary": bool, "messages": bool, "contacts": bool,
                 "domains": bool, "attachments": bool, "body": bool,
                 "eml_path": bool, "all_contacts": bool, "charts": bool},
    "columns":  {"date": bool, "direction": bool, "sender_name": bool, ...},
    "lang":     "fa" | "en"
  }
"""

import datetime as dt
import email.header
import json
import os
import quopri
import re

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import config

# Characters Excel forbids inside text cells: C0 except TAB/LF/CR, plus DEL.
_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
EXCEL_CELL_LIMIT = 32767
# Bodies are kept well inside the 32 767 character cell limit so the
# truncation marker still fits and the report stays reasonably sized.
BODY_LIMIT = 20000

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_ENCODED_WORD_RE = re.compile(r"=\?[^?]*\?[BbQq]\?[^?]*\?=")
_HEX_ESCAPE_RE = re.compile(r"=[0-9A-Fa-f]{2}")

# Domains of well-known public email services. They are shared by unrelated
# users, so a report never presents one as an organization; they are shown
# separately under their own type.
PUBLIC_PROVIDERS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "ymail.com",
    "rocketmail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "aol.com", "icloud.com", "me.com", "mac.com", "proton.me",
    "protonmail.com", "tutanota.com", "gmx.com", "gmx.de", "web.de",
    "zoho.com", "mail.ru", "yandex.com", "rambler.ru", "163.com", "126.com",
    "qq.com", "naver.com", "daum.net", "seznam.cz", "wp.pl", "op.pl",
    "onet.pl", "orange.fr", "free.fr", "laposte.net", "libero.it",
    "terra.com.br", "uol.com.br", "bigpond.com", "xtra.co.nz",
    "sympatico.ca", "rogers.com", "shaw.ca", "comcast.net", "verizon.net",
    "att.net", "bellsouth.net", "cox.net", "earthlink.net", "charter.net",
    "sbcglobal.net", "hetnet.nl", "planet.nl", "facebook.com",
})

# Folders that hold unsent messages. A draft is reported in its own category
# and is never counted as a sent message.
_DRAFT_RE = re.compile(r"(^|\.)drafts?$", re.IGNORECASE)


# --------------------------------------------------------------------- decoding

def decode_words(value):
    """Decode RFC 2047 encoded-words; leave everything else untouched."""
    if not value or not isinstance(value, str):
        return value or ""
    if "=?" not in value:
        return value
    try:
        parts = email.header.decode_header(value)
    except Exception:
        return value
    out = []
    for text, charset in parts:
        if isinstance(text, bytes):
            out.append(text.decode(charset or "utf-8", "replace"))
        else:
            out.append(text)
    return "".join(out)


def _looks_quoted_printable(text: str) -> bool:
    """Heuristic for stored text that was never QP-decoded.

    Requires either RFC 2045 soft line breaks or a clear majority of
    hex escapes among all '=' characters, so ordinary text that simply
    contains a fragment like '=3D' is never altered.
    """
    if "=\n" in text or "=\r" in text:
        return True
    escapes = len(_HEX_ESCAPE_RE.findall(text))
    return escapes >= 4 and escapes >= text.count("=") - 1


def readable_text(value):
    """Human-readable display text for one stored archive value.

    Repairs RFC 2047 encoded-words and quoted-printable payloads left over
    from older MailDaan archives. Display-only: the archive is untouched.
    """
    text = decode_words(value)
    if not text or not isinstance(text, str):
        return text or ""
    if _looks_quoted_printable(text):
        try:
            decoded = quopri.decodestring(
                text.encode("utf-8", "replace")).decode("utf-8", "replace")
        except Exception:
            return text
        if decoded and decoded != text:
            return decoded
    return text


def sanitize_cell(value, limit=True):
    """Clean a value for safe Excel writing without altering source data."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    text = _ILLEGAL_RE.sub("", text)
    text = text.replace("\r", "")
    if limit and len(text) > EXCEL_CELL_LIMIT:
        text = text[:EXCEL_CELL_LIMIT - 12] + "\n[…]"
    # never let a value be interpreted as a formula
    if text[:1] in ("=", "+", "-", "@"):
        text = "​" + text
    return text


# ------------------------------------------------------------------- calendars

def gregorian_to_jalali(gy: int, gm: int, gd: int):
    """Gregorian date -> Solar Hijri (Jalali) date as (year, month, day).

    Classic 979/12053 algorithm (Pournader/Toossi): days are counted from
    1600-03-01 (Gregorian) == 979-01-01 (Jalali).
    """
    g_days_in_month = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    j_days_in_month = (31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29)

    gy2 = gy - 1600
    g_day_no = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    for i in range(gm - 1):
        g_day_no += g_days_in_month[i]
    if gm > 2 and ((gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0):
        g_day_no += 1
    g_day_no += gd - 1

    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461

    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365

    jm, jd = 1, j_day_no + 1
    for i in range(12):
        if j_day_no < j_days_in_month[i]:
            jm, jd = i + 1, j_day_no + 1
            break
        j_day_no -= j_days_in_month[i]
    return jy, jm, jd


def fa_digits(text) -> str:
    return str(text).translate(_FA_DIGITS)


def jalali_text(value, with_time=True) -> str:
    """Zero-padded Jalali text for a stored 'YYYY-MM-DD HH:MM:SS' value.

    The fixed width keeps the column lexicographically sortable in Excel.
    """
    parsed = parse_date(value)
    if parsed is None:
        return ""
    jy, jm, jd = gregorian_to_jalali(parsed.year, parsed.month, parsed.day)
    text = f"{jy:04d}/{jm:02d}/{jd:02d}"
    if with_time:
        text += f" {parsed.hour:02d}:{parsed.minute:02d}"
    return fa_digits(text)


def parse_date(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return dt.datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


# ---------------------------------------------------------------------- locale

def load_locale(lang: str) -> dict:
    path = os.path.join(config.get_ui_dir(), "locales", f"{lang or 'en'}.json")
    fallback = os.path.join(config.get_ui_dir(), "locales", "en.json")
    for candidate in (path, fallback):
        if os.path.exists(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                pass
    return {}


# ---------------------------------------------------------------------- styling

_TITLE_COLOR = "FF1F4E79"
_HEADER_COLOR = "FF1F4E79"
_SECTION_COLOR = "FF2E5A88"
_BAND_COLOR = "FFDCE6F1"


class Style:
    """Language-dependent workbook typography and direction."""

    def __init__(self, lang: str):
        self.fa = (lang == "fa")
        self.font_name = "Tahoma" if self.fa else "Calibri"
        self.t = load_locale(lang).get("excel", {})
        self.rtl = self.fa
        self.text_align = "right" if self.fa else "left"
        self.base = Font(name=self.font_name, size=10)
        self.base_bold = Font(name=self.font_name, size=10, bold=True)
        self.title = Font(name=self.font_name, size=16, bold=True,
                          color=_TITLE_COLOR)
        self.subtitle = Font(name=self.font_name, size=10,
                             color="FF5A6B7E")
        self.section = Font(name=self.font_name, size=12, bold=True,
                            color=_SECTION_COLOR)
        self.header_font = Font(name=self.font_name, size=10, bold=True,
                                color="FFFFFFFF")
        self.header_fill = PatternFill("solid", start_color=_HEADER_COLOR)
        self.title_fill = PatternFill("solid", start_color=_BAND_COLOR)
        self.value_font = Font(name=self.font_name, size=10, bold=True,
                               color=_TITLE_COLOR)

    def tr(self, key, fallback=""):
        value = self.t.get(key, fallback)
        return value if value else fallback


def _set_widths(ws, widths):
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width


def _write_header(ws, row, headers, style, height=24):
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col_idx, value=header)
        cell.font = style.header_font
        cell.fill = style.header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
    ws.row_dimensions[row].height = height


def _value_cell(ws, row, col, value, style, *, num=False, date=False,
                wrap=False, ltr=False, center=False, bold=False):
    """Write one styled data cell (numbers stay numeric, dates stay dates)."""
    cell = ws.cell(row=row, column=col)
    if date and value:
        parsed = parse_date(value)
        if parsed is not None:
            cell.value = parsed
            cell.number_format = "yyyy-mm-dd hh:mm"
            cell.alignment = Alignment(horizontal=style.text_align)
            cell.font = style.base
            return cell
    if num and isinstance(value, (int, float)):
        cell.value = value
        cell.number_format = "#,##0"
    else:
        cell.value = sanitize_cell(readable_text(value))
    horizontal = "center" if center else ("left" if ltr else style.text_align)
    cell.alignment = Alignment(horizontal=horizontal, vertical="top",
                               wrap_text=bool(wrap))
    cell.font = style.base_bold if bold else style.base
    return cell


def _finish_table(ws, row, ncols, style, widths, first_row=1):
    """Freeze the header, enable autofilter and set print layout."""
    _set_widths(ws, widths)
    ws.freeze_panes = f"A{row + 1}"
    if row >= first_row:
        ws.auto_filter.ref = (f"{get_column_letter(1)}{first_row}:"
                              f"{get_column_letter(ncols)}{max(row, first_row)}")
    ws.sheet_view.rightToLeft = style.rtl
    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = f"{first_row}:{first_row}"


def _value_cell(ws, row, col, value, style, *, num=False, date=False,
                wrap=False, ltr=False, center=False, bold=False):
    """Write one styled data cell (numbers stay numeric, dates stay dates)."""
    cell = ws.cell(row=row, column=col)
    if date and value:
        parsed = parse_date(value)
        if parsed is not None:
            cell.value = parsed
            cell.number_format = "yyyy-mm-dd hh:mm"
            cell.alignment = Alignment(horizontal=style.text_align)
            cell.font = style.base
            return cell
    if num and isinstance(value, (int, float)):
        cell.value = value
        cell.number_format = "#,##0"
    else:
        cell.value = sanitize_cell(readable_text(value))
    horizontal = "center" if center else ("left" if ltr else style.text_align)
    cell.alignment = Alignment(horizontal=horizontal, vertical="top",
                               wrap_text=bool(wrap))
    cell.font = style.base_bold if bold else style.base
    return cell


# --------------------------------------------------------------------- scoping

def own_addresses(account) -> set:
    """Addresses of the selected account (email plus any registered alias).

    Incoming/outgoing direction is derived from the account's own addresses;
    a message the account did not send is incoming.
    """
    addresses = set()
    email = (account or {}).get("email") or ""
    if email:
        addresses.add(email.strip().lower())
    for alias in (account or {}).get("aliases") or []:
        if alias:
            addresses.add(str(alias).strip().lower())
    return {a for a in addresses if a}


def message_direction(row, addresses) -> str:
    """'incoming' | 'outgoing' | 'draft' for one archive message row.

    Drafts are detected from folder membership and always reported as their
    own category, never as sent messages.
    """
    names = []
    for key in ("folder_imap", "folder_display"):
        value = row.get(key)
        if value:
            names.extend(str(value).split(","))
    if any(_DRAFT_RE.search(str(n).strip()) for n in names if n):
        return "draft"
    sender = (row.get("sender_email") or "").strip().lower()
    if sender and sender in addresses:
        return "outgoing"
    return "incoming"


def resolve_scope(scope_cfg: dict) -> dict:
    """Normalize the dialog scope into the storage query scope.

    Date ranges are computed in UTC so the report matches the stored
    `date_ts` (epoch seconds) exactly.
    """
    scope_cfg = scope_cfg or {}
    scope = {
        "folder_ids": scope_cfg.get("folder_ids") or [],
        "party": None,
        "search": (scope_cfg.get("keyword") or "").strip() or None,
    }
    now = dt.datetime.now(dt.timezone.utc)
    rng = scope_cfg.get("range") or "all"
    if rng == "30d":
        scope["date_from"] = int((now - dt.timedelta(days=30)).timestamp())
        scope["date_to"] = None
    elif rng == "year":
        scope["date_from"] = int((now - dt.timedelta(days=365)).timestamp())
        scope["date_to"] = None
    elif rng == "custom":
        for key, sign in (("date_from", 1), ("date_to", -1)):
            value = scope_cfg.get(key)
            parsed = None
            if value:
                try:
                    parsed = dt.datetime.strptime(
                        str(value)[:10], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
                except ValueError:
                    parsed = None
            if parsed is None:
                # An incomplete custom range deliberately matches everything
                # on that side instead of silently dropping all messages.
                scope["date_from" if sign > 0 else "date_to"] = None
            elif sign > 0:
                scope["date_from"] = int(parsed.timestamp())
            else:
                scope["date_to"] = int((parsed + dt.timedelta(days=1)
                                        - dt.timedelta(seconds=1)).timestamp())
    else:
        scope["date_from"] = None
        scope["date_to"] = None
    return scope


def filter_messages(rows, direction, include_drafts, addresses):
    """Apply direction/drafts handling to the already scoped messages."""
    kept = []
    for row in rows:
        way = message_direction(row, addresses)
        if way == "draft":
            if not include_drafts:
                continue
        elif direction == "incoming" and way != "incoming":
            continue
        elif direction == "outgoing" and way != "outgoing":
            continue
        row["direction"] = way
        kept.append(row)
    return kept


# The dialog sends mixed camelCase/snake_case keys (emlPath, allContacts,
# senderName …). Normalize once so every lookup below uses snake_case.
_CAMEL_TO_SNAKE = {
    "emlPath": "eml_path", "allContacts": "all_contacts",
    "senderName": "sender_name", "senderEmail": "sender_email",
}


def _norm_keys(mapping):
    return {_CAMEL_TO_SNAKE.get(key, key): value
            for key, value in (mapping or {}).items()}


def scope_description(scope_cfg, locale, account) -> str:
    """One localized sentence describing the applied reporting scope."""
    d = locale.get("reportDialog", {})
    parts = []
    email = (account or {}).get("email") or ""
    if email:
        parts.append(f"{d.get('account', 'Account')}: {email}")
    ranges = {"all": "rangeAll", "30d": "range30", "year": "rangeYear",
              "custom": "rangeCustom"}
    rng = scope_cfg.get("range") or "all"
    parts.append(d.get(ranges.get(rng, "rangeAll"), "All time"))
    if rng == "custom":
        parts.append(f"{scope_cfg.get('date_from', '')} – {scope_cfg.get('date_to', '')}")
    folder_ids = scope_cfg.get("folder_ids") or []
    parts.append(f"{len(folder_ids)} {d.get('folders', 'Mail folders')}"
                 if folder_ids else d.get("appliedFolders", "All folders"))
    dirs = {"both": "dirBoth", "incoming": "dirIncoming",
            "outgoing": "dirOutgoing"}
    parts.append(d.get(dirs.get(scope_cfg.get("direction") or "both", "dirBoth"),
                       "Incoming and outgoing"))
    parts.append(d.get("draftsIncluded" if scope_cfg.get("include_drafts")
                       else "draftsExcluded", "Drafts"))
    keyword = (scope_cfg.get("keyword") or "").strip()
    if keyword:
        parts.append(f"{d.get('keyword', 'Keyword')}: “{keyword}”")
    return " • ".join(str(p) for p in parts)


# ------------------------------------------------------------------- analytics

def monthly_activity(messages) -> list:
    """(month key, total, incoming, outgoing) sorted chronologically."""
    counts = {}
    for m in messages:
        parsed = parse_date(m.get("date_utc"))
        if parsed is None:
            continue
        key = parsed.strftime("%Y-%m")
        total, inc, out = counts.get(key, (0, 0, 0))
        total += 1
        if m.get("direction") == "outgoing":
            out += 1
        else:
            inc += 1
        counts[key] = (total, inc, out)
    return [(key, *counts[key]) for key in sorted(counts)]


def choose_display_name(names, email) -> str:
    """Best display name for a contact: the longest non-empty one seen."""
    candidates = [str(n).strip() for n in str(names or "").split(",")
                  if n and str(n).strip()]
    candidates = [c for c in candidates if c.lower() != email]
    if not candidates:
        return ""
    return sorted(candidates, key=lambda c: (-len(c), c))[0]


def contact_rows(db, message_ids, include_directory):
    """Contacts of the selected messages, optionally merged with the full
    account directory (rows without activity are clearly marked)."""
    rows = []
    active = {c["email"]: c for c in db.report_contacts(message_ids)}
    for contact in active.values():
        contact["display_name"] = choose_display_name(
            contact.get("names"), contact.get("email"))
        contact["in_scope"] = True
        rows.append(contact)
    if include_directory:
        for contact in db.report_contact_directory():
            email = (contact.get("email_norm") or "").strip().lower()
            if email in active:
                continue
            rows.append({
                "email": email, "display_name": contact.get("display_name") or "",
                "domain": contact.get("domain") or "",
                "messages": 0, "outgoing": 0, "incoming": 0,
                "first_seen": contact.get("first_seen") or "",
                "last_seen": contact.get("last_seen") or "",
                "in_scope": False,
            })
    return rows


# ------------------------------------------------------------------- worksheets

def _title_block(ws, style, lines, ncols):
    """Report title area: colored band, title, subtitle and scope line."""
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    cell = ws.cell(row=1, column=1, value=lines[0])
    cell.font = style.title
    cell.fill = style.title_fill
    cell.alignment = Alignment(horizontal=style.text_align, vertical="center",
                               indent=1)
    ws.row_dimensions[1].height = 30
    row = 2
    for line in lines[1:]:
        ws.merge_cells(start_row=row, start_column=1, end_row=row,
                       end_column=ncols)
        cell = ws.cell(row=row, column=1, value=line)
        cell.font = style.subtitle
        cell.alignment = Alignment(horizontal=style.text_align, vertical="center",
                                   indent=1, wrap_text=False)
        ws.row_dimensions[row].height = 16
        row += 1
    return row + 1


def _section(ws, row, text, style, ncols):
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = style.section
    cell.alignment = Alignment(horizontal=style.text_align)
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
    return row + 1


def _kv_row(ws, row, label, value, style, value_is_number=False):
    _value_cell(ws, row, 1, label, style, bold=True)
    cell = _value_cell(ws, row, 2, value, style)
    if value_is_number and isinstance(value, (int, float)):
        cell.number_format = "#,##0"
        cell.alignment = Alignment(horizontal=style.text_align)
        cell.font = style.value_font
    else:
        cell.font = style.value_font
    return row + 1


def _write_summary(wb, style, ctx, with_charts):
    t = style.t
    stats = ctx["stats"]
    ws = wb.create_sheet(t.get("sheetSummary", "Executive Summary"))
    ws.sheet_view.showGridLines = False
    ws.sheet_view.rightToLeft = style.rtl
    ncols = 6
    row = _title_block(ws, style, [
        t.get("reportTitle", "MailDaan Correspondence Report"),
        t.get("subtitle", "Offline email archive analysis"),
        f"{t.get('account', 'Account')}: {ctx['account_label']}",
        f"{t.get('generatedAt', 'Generated')}: {ctx['generated']}",
        f"{t.get('scope', 'Scope')}: {ctx['scope_label']}",
    ], ncols)

    # ------------------------------------------------------- key metrics
    row = _section(ws, row, t.get("metricsTitle", "Key metrics"), style, ncols)
    metrics = [
        (t.get("totalMessages", "Total matching messages"), stats["total"], True),
        (t.get("incoming", "Incoming"), stats["incoming"], True),
        (t.get("outgoing", "Outgoing"), stats["outgoing"], True),
        (t.get("drafts", "Drafts"), stats["drafts"], True),
        (t.get("uniqueCorrespondents", "Unique correspondents"), stats["contacts"], True),
        (t.get("uniqueDomains", "Relevant email domains"), stats["domains"], True),
        (t.get("customDomains", "Custom domains"), stats["custom_domains"], True),
        (t.get("publicProviders", "Public email providers"), stats["public_providers"], True),
        (t.get("attachments", "Attachments"), stats["attachments"], True),
        (t.get("attachmentVolume", "Attachment volume (KB)"),
         round(stats["attachment_bytes"] / 1024, 1), True),
        (t.get("firstMessage", "First message"), stats["first"], False),
        (t.get("latestMessage", "Latest message"), stats["latest"], False),
    ]
    for label, value, is_number in metrics:
        row = _kv_row(ws, row, label, value, style, value_is_number=is_number)

    # -------------------------------------------------- activity by month
    row += 1
    row = _section(ws, row, t.get("monthlyTitle", "Activity by month"),
                   style, ncols)
    header_row = row
    _write_header(ws, row, [
        t.get("month", "Month"), t.get("messagesCol", "Messages"),
        t.get("monthIncoming", "Incoming"), t.get("monthOutgoing", "Outgoing"),
    ], style)
    row += 1
    months = ctx["months"]
    first_data_row = row
    for month, total, incoming, outgoing in months:
        _value_cell(ws, row, 1, month, style, ltr=True)
        _value_cell(ws, row, 2, total, style, num=True, center=True)
        _value_cell(ws, row, 3, incoming, style, num=True, center=True)
        _value_cell(ws, row, 4, outgoing, style, num=True, center=True)
        row += 1
    _set_widths(ws, [36, 22, 15, 15, 14, 14])
    if with_charts and len(months) >= 2:
        chart = BarChart()
        chart.type = "col"
        chart.style = 10
        chart.title = t.get("monthlyTitle", "Activity by month")
        chart.y_axis.title = t.get("messagesCol", "Messages")
        data = Reference(ws, min_col=2, max_col=4,
                         min_row=header_row, max_row=max(row - 1, header_row))
        cats = Reference(ws, min_col=1, min_row=first_data_row,
                         max_row=max(row - 1, first_data_row))
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        chart.width = 16
        chart.height = 8
        chart.legend.position = "b"
        ws.add_chart(chart, f"{get_column_letter(ncols + 1)}2")
    row += 1

    # --------------------------------------------- most frequent contacts
    row = _section(ws, row, t.get("topCorrespondents", "Most frequent correspondents"),
                   style, ncols)
    _write_header(ws, row, [
        t.get("correspondent", "Correspondent"), t.get("email", "Email address"),
        t.get("messagesCount", "Messages"),
    ], style)
    row += 1
    for contact in ctx["top_contacts"]:
        _value_cell(ws, row, 1, contact["display"] or contact["email"], style)
        _value_cell(ws, row, 2, contact["email"], style, ltr=True)
        _value_cell(ws, row, 3, contact["messages"], style, num=True, center=True)
        row += 1

    # -------------------------------------------------- most active domains
    row += 1
    row = _section(ws, row, t.get("topDomains", "Most active email domains"),
                   style, ncols)
    _write_header(ws, row, [
        t.get("domain", "Domain"), t.get("domainType", "Domain type"),
        t.get("messagesCount", "Messages"),
    ], style)
    row += 1
    for domain in ctx["top_domains"]:
        _value_cell(ws, row, 1, domain["domain"], style, ltr=True)
        _value_cell(ws, row, 2, domain["type"], style)
        _value_cell(ws, row, 3, domain["messages"], style, num=True, center=True)
        row += 1
    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    return ws


def _write_messages(wb, style, ctx):
    t = style.t
    columns = ctx["columns"]
    content = ctx["content"]
    ws = wb.create_sheet(t.get("sheetMessages", "Messages"))

    # (key, header, width, flags) of the selected message columns.
    cols = []

    def add(key, header, width, **flags):
        cols.append((key, header, width, flags))

    if columns.get("date", True):
        if style.fa:
            add("jalali", t.get("dateJalali", "Persian date"), 19, ltr=True)
            add("date_utc", t.get("dateGreg", "Gregorian date"), 18, date=True)
        else:
            add("date_utc", t.get("dateGreg", "Date"), 18, date=True)
    if columns.get("direction", True):
        add("direction", t.get("direction", "Direction"), 12, center=True)
    if columns.get("sender_name", True):
        add("sender_name", t.get("senderName", "Sender"), 26)
    if columns.get("sender_email", True):
        add("sender_email", t.get("senderEmail", "Sender email"), 30, ltr=True)
    if columns.get("recipients", True):
        add("recipients", t.get("recipients", "Recipients"), 36)
    if columns.get("cc", False):
        add("cc", t.get("cc", "CC"), 26)
    if columns.get("bcc", False):
        add("bcc", t.get("bcc", "BCC"), 26)
    if columns.get("subject", True):
        add("subject", t.get("subject", "Subject"), 48, wrap=True)
    if columns.get("folders", True):
        add("folders", t.get("folders", "Folders"), 24)
    if columns.get("size", True):
        add("size", t.get("sizeKB", "Size (KB)"), 12, kb=True)
    if columns.get("attachments", True):
        add("attachment_count", t.get("attachmentCount", "Attachments"), 12,
            num=True, center=True)
        if content.get("attachments"):
            add("attachment_names", t.get("attachmentNames", "Attachment names"),
                36)
    if content.get("body"):
        add("body", t.get("body", "Body"), 70, body=True)
    if content.get("eml_path"):
        add("eml_relpath", t.get("emlPath", "Original EML file"), 44, ltr=True)

    row = 1
    _write_header(ws, row, [header for _, header, _, _ in cols], style)
    row += 1
    direction_labels = {
        "incoming": t.get("dirIncoming", "Incoming"),
        "outgoing": t.get("dirOutgoing", "Outgoing"),
        "draft": t.get("dirDraft", "Draft"),
    }
    groups = ctx["recipient_lists"]
    for message in ctx["messages"]:
        enriched = dict(message)
        group = groups.get(message["id"], {})
        # The stored column is `plain_body`; the selected report column
        # presents it as the human-readable body.
        enriched["body"] = message.get("plain_body") or ""
        enriched["jalali"] = jalali_text(message.get("date_utc"))
        enriched["recipients"] = group.get("to", "")
        enriched["cc"] = group.get("cc", "")
        enriched["bcc"] = group.get("bcc", "")
        enriched["folders"] = ctx["folder_names"](message)
        enriched["attachment_count"] = ctx["attachment_counts"].get(message["id"], 0)
        enriched["attachment_names"] = ctx["attachment_names"].get(message["id"], "")
        enriched["size"] = message.get("size") or 0
        enriched["direction"] = direction_labels.get(message.get("direction"), "")
        for col_idx, (key, _, _, flags) in enumerate(cols, start=1):
            value = enriched.get(key, "")
            if flags.get("body"):
                text = sanitize_cell(readable_text(value), limit=False)
                if len(text) > BODY_LIMIT:
                    marker = t.get("bodyTruncated", "(truncated)")
                    text = text[:BODY_LIMIT] + "\n" + marker
                cell = ws.cell(row=row, column=col_idx, value=sanitize_cell(text))
                cell.alignment = Alignment(horizontal=style.text_align,
                                           vertical="top", wrap_text=True)
                cell.font = style.base
            elif flags.get("kb"):
                cell = _value_cell(ws, row, col_idx,
                                   round((value or 0) / 1024, 1), style,
                                   num=True, center=True)
                cell.number_format = "#,##0.0"
            else:
                _value_cell(ws, row, col_idx, value, style,
                            ltr=flags.get("ltr"), wrap=flags.get("wrap"),
                            date=flags.get("date"), num=flags.get("num"),
                            center=flags.get("center"))
        row += 1
    _finish_table(ws, row - 1, len(cols), style,
                  [width for _, _, width, _ in cols])
    return ws


def _write_contacts(wb, style, ctx):
    t = style.t
    ws = wb.create_sheet(t.get("sheetContacts", "Contacts"))
    headers = [t.get("name", "Name"), t.get("email", "Email address"),
               t.get("domain", "Domain"), t.get("messagesCount", "Messages"),
               t.get("incomingCount", "Incoming"),
               t.get("outgoingCount", "Outgoing"),
               t.get("firstSeen", "First message"),
               t.get("lastSeen", "Last message")]
    widths = [28, 34, 24, 12, 12, 12, 20, 20]
    if style.fa:
        # Jalali text (sortable by construction) next to the sortable
        # Gregorian value keeps Persian dates readable without breaking
        # filtering and sorting.
        headers = [t.get("name", "Name"), t.get("email", "Email address"),
                   t.get("domain", "Domain"), t.get("messagesCount", "Messages"),
                   t.get("incomingCount", "Incoming"),
                   t.get("outgoingCount", "Outgoing"),
                   t.get("firstSeen", "First message"), t.get("dateGreg", "Gregorian date"),
                   t.get("lastSeen", "Last message"), t.get("dateGreg", "Gregorian date")]
        widths = [28, 34, 24, 12, 12, 12, 19, 18, 19, 18]
    row = 1
    _write_header(ws, row, headers, style)
    row += 1
    for contact in ctx["contacts"]:
        _value_cell(ws, row, 1, contact.get("display_name")
                    or t.get("noName", "(no display name)"), style)
        _value_cell(ws, row, 2, contact.get("email") or "", style, ltr=True)
        _value_cell(ws, row, 3, contact.get("domain") or "", style, ltr=True)
        _value_cell(ws, row, 4, contact.get("messages") or 0, style,
                    num=True, center=True)
        _value_cell(ws, row, 5, contact.get("incoming") or 0, style,
                    num=True, center=True)
        _value_cell(ws, row, 6, contact.get("outgoing") or 0, style,
                    num=True, center=True)
        if style.fa:
            _value_cell(ws, row, 7, jalali_text(contact.get("first_seen")),
                        style, ltr=True)
            _value_cell(ws, row, 8, contact.get("first_seen") or "", style,
                        date=True)
            _value_cell(ws, row, 9, jalali_text(contact.get("last_seen")),
                        style, ltr=True)
            _value_cell(ws, row, 10, contact.get("last_seen") or "", style,
                        date=True)
        else:
            _value_cell(ws, row, 7, contact.get("first_seen") or "", style,
                        date=True)
            _value_cell(ws, row, 8, contact.get("last_seen") or "", style,
                        date=True)
        if contact.get("in_scope") is False:
            for col_idx in range(1, len(headers) + 1):
                ws.cell(row=row, column=col_idx).font = style.subtitle
        row += 1
    _finish_table(ws, row - 1, len(headers), style, widths)
    return ws


def _write_domains(wb, style, ctx):
    t = style.t
    ws = wb.create_sheet(t.get("sheetDomains", "Email Domains"))
    headers = [t.get("domain", "Domain"), t.get("domainType", "Domain type"),
               t.get("uniqueCorrespondents", "Contacts"),
               t.get("messagesCount", "Messages"),
               t.get("firstSeen", "First message"),
               t.get("lastSeen", "Last message")]
    widths = [30, 24, 14, 14, 20, 20]
    row = 1
    _write_header(ws, row, headers, style)
    row += 1
    for domain in ctx["domains"]:
        _value_cell(ws, row, 1, domain.get("domain") or "", style, ltr=True)
        _value_cell(ws, row, 2, domain.get("type") or "", style)
        _value_cell(ws, row, 3, domain.get("contacts") or 0, style,
                    num=True, center=True)
        _value_cell(ws, row, 4, domain.get("messages") or 0, style,
                    num=True, center=True)
        if style.fa:
            _value_cell(ws, row, 5, jalali_text(domain.get("first_seen")),
                        style, ltr=True)
            _value_cell(ws, row, 6, jalali_text(domain.get("last_seen")),
                        style, ltr=True)
        else:
            _value_cell(ws, row, 5, domain.get("first_seen") or "", style,
                        date=True)
            _value_cell(ws, row, 6, domain.get("last_seen") or "", style,
                        date=True)
        row += 1
    _finish_table(ws, row - 1, len(headers), style, widths)
    return ws


def _write_attachments(wb, style, ctx):
    t = style.t
    ws = wb.create_sheet(t.get("sheetAttachments", "Attachments"))
    if style.fa:
        headers = [t.get("dateJalali", "Persian date"),
                   t.get("dateGreg", "Gregorian date"),
                   t.get("subject", "Subject"), t.get("senderName", "Sender"),
                   t.get("fileName", "File name"), t.get("fileType", "Type"),
                   t.get("sizeCol", "Size (KB)")]
        widths = [19, 18, 44, 24, 34, 18, 12]
    else:
        headers = [t.get("dateGreg", "Date"), t.get("subject", "Subject"),
                   t.get("senderName", "Sender"),
                   t.get("fileName", "File name"), t.get("fileType", "Type"),
                   t.get("sizeCol", "Size (KB)")]
        widths = [18, 44, 24, 34, 18, 12]
    row = 1
    _write_header(ws, row, headers, style)
    row += 1
    for attachment in ctx["attachments"]:
        col = 1
        if style.fa:
            _value_cell(ws, row, col, jalali_text(attachment.get("date_utc")),
                        style, ltr=True)
            col += 1
        _value_cell(ws, row, col, attachment.get("date_utc") or "", style,
                    date=True)
        col += 1
        _value_cell(ws, row, col, attachment.get("subject") or "", style,
                    wrap=True)
        col += 1
        _value_cell(ws, row, col, attachment.get("sender_name")
                    or attachment.get("sender_email") or "", style)
        col += 1
        _value_cell(ws, row, col, attachment.get("filename") or "", style)
        col += 1
        _value_cell(ws, row, col, attachment.get("mime_type") or "", style,
                    ltr=True)
        col += 1
        cell = _value_cell(ws, row, col, round(
            (attachment.get("bytes") or 0) / 1024, 1), style, num=True,
            center=True)
        cell.number_format = "#,##0.0"
        row += 1
    _finish_table(ws, row - 1, len(headers), style, widths)
    return ws


def _write_info(wb, style, ctx):
    t = style.t
    ws = wb.create_sheet(t.get("sheetInfo", "Report Information"))
    ws.sheet_view.showGridLines = False
    ws.sheet_view.rightToLeft = style.rtl
    row = _title_block(ws, style, [
        t.get("sheetInfo", "Report Information"),
        f"{t.get('generatedAt', 'Generated')}: {ctx['generated']}",
    ], 2)
    purpose_keys = {
        "comprehensive": "purposeComprehensive", "activity": "purposeActivity",
        "contacts": "purposeContacts", "domains": "purposeDomains",
        "messages": "purposeMessages",
    }
    d = load_locale("fa" if style.fa else "en").get("reportDialog", {})
    scope_cfg = ctx["scope_cfg"]
    dirs = {"both": "dirBoth", "incoming": "dirIncoming",
            "outgoing": "dirOutgoing"}
    folder_ids = scope_cfg.get("folder_ids") or []
    folder_counts = (d.get("appliedFolders", "All folders")
                     if not folder_ids else
                     "{}: {}".format(d.get("folders", "Mail folders"),
                                     len(folder_ids)))
    keyword = (scope_cfg.get("keyword") or "").strip()
    rows = [
        (t.get("infoLanguage", "Report language"),
         "فارسی" if style.fa else "English"),
        (t.get("infoPurpose", "Report purpose"),
         d.get(purpose_keys.get(ctx["purpose"], "purposeComprehensive"),
               "Comprehensive Report")),
        (t.get("account", "Account"), ctx["account_label"]),
        (t.get("infoFolders", "Folders"), folder_counts),
        (t.get("infoDirection", "Direction"),
         d.get(dirs.get(scope_cfg.get("direction") or "both", "dirBoth"),
               "Incoming and outgoing")),
        (t.get("infoDrafts", "Drafts"),
         d.get("draftsIncluded" if scope_cfg.get("include_drafts")
               else "draftsExcluded", "Drafts")),
        (t.get("infoKeyword", "Keyword filter"),
         keyword if keyword else "—"),
        (t.get("scope", "Scope"), ctx["scope_label"]),
        (t.get("infoRows", "Rows per worksheet"), ctx["row_counts"]),
        (t.get("infoVersion", "Application version"),
         getattr(config, "APP_VERSION", "")),
    ]
    for label, value in rows:
        _value_cell(ws, row, 1, label, style, bold=True)
        cell = _value_cell(ws, row, 2, value, style)
        cell.alignment = Alignment(horizontal=style.text_align,
                                   vertical="top", wrap_text=True)
        row += 1
    row += 1
    notes = list(ctx["notes"])
    if not notes:
        notes = [t.get("noteProvider", "")]
    _value_cell(ws, row, 1, t.get("infoNotes", "Notes"), style, bold=True)
    row += 1
    for note in notes:
        if not note:
            continue
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        cell = ws.cell(row=row, column=1, value=note)
        cell.font = style.base
        cell.alignment = Alignment(horizontal=style.text_align, vertical="top",
                                   wrap_text=True)
        ws.row_dimensions[row].height = 30
        row += 1
    _set_widths(ws, [30, 90])
    return ws


# ---------------------------------------------------------------- main entry

def build_report(db, options: dict, lang: str, account, path: str) -> dict:
    """Create the configured Excel report at `path`. Returns a summary."""
    options = options or {}
    style = Style(lang)
    t = style.t
    scope_cfg = options.get("scope") or {}
    # Mixed-case keys from the dialog are normalized once, here.
    content = _norm_keys(options.get("content"))
    columns = _norm_keys(options.get("columns"))
    purpose = options.get("purpose") or "comprehensive"
    locale = load_locale(lang)

    addresses = own_addresses(account)
    scope = resolve_scope(scope_cfg)
    direction = scope_cfg.get("direction") or "both"
    include_drafts = bool(scope_cfg.get("include_drafts"))

    # ---- one filtered message set backs every worksheet of this report
    candidates = db.report_messages(scope, with_body=bool(content.get("body")))
    messages = filter_messages(candidates, direction, include_drafts,
                               addresses)
    ids = [m["id"] for m in messages]

    contacts = contact_rows(db, ids, bool(content.get("all_contacts"))) \
        if content.get("contacts") or content.get("summary") else []

    recipient_lists = {}
    for recipient in db.report_recipients_for(ids) if ids else []:
        group = recipient_lists.setdefault(recipient["message_id"],
                                           {"to": [], "cc": [], "bcc": []})
        kind = recipient["rtype"] if recipient["rtype"] in group else "to"
        text = readable_text(recipient.get("name") or "")
        address = readable_text(recipient.get("email") or "")
        if text and address:
            group[kind].append(f"{text} <{address}>")
        elif address:
            group[kind].append(address)
    for groups in recipient_lists.values():
        for key in groups:
            groups[key] = "; ".join(groups[key])

    attachments = db.report_attachments(ids) if ids else []
    attachment_counts, attachment_names, attachment_bytes = {}, {}, 0
    for att in attachments:
        mid = att["message_id"]
        attachment_counts[mid] = attachment_counts.get(mid, 0) + 1
        name = readable_text(att.get("filename") or att.get("filename_safe") or "")
        if name:
            names = attachment_names.setdefault(mid, [])
            names.append(name)
        attachment_bytes += att.get("bytes") or 0
    for mid, names in attachment_names.items():
        attachment_names[mid] = ", ".join(names)

    months = monthly_activity(messages)
    dates = [parse_date(m.get("date_utc")) for m in messages]
    dates = [d for d in dates if d is not None]
    provider_label = t.get("providerPublic", "Public email provider")
    custom_label = t.get("providerCustom", "Custom domain")
    domains = db.report_domains(ids) if ids else []
    for domain in domains:
        domain["type"] = (provider_label
                          if domain.get("domain") in PUBLIC_PROVIDERS
                          else custom_label)
    top_domains = [
        {"domain": d.get("domain"), "messages": d.get("messages") or 0,
         "type": provider_label if d.get("domain") in PUBLIC_PROVIDERS
                 else custom_label}
        for d in sorted(domains, key=lambda x: x.get("messages") or 0,
                        reverse=True)[:10]
    ]
    top_contacts = [
        {"email": c.get("email"), "messages": c.get("messages") or 0,
         "display": c.get("display_name") or ""}
        for c in sorted(contacts, key=lambda x: x.get("messages") or 0,
                        reverse=True)[:10]
    ]

    folder_labels = locale.get("folders", {}).get("map", {})
    stats = {
        "total": len(messages),
        "incoming": sum(1 for m in messages if m.get("direction") == "incoming"),
        "outgoing": sum(1 for m in messages if m.get("direction") == "outgoing"),
        "drafts": sum(1 for m in messages if m.get("direction") == "draft"),
        "contacts": len(contacts),
        "domains": len(domains),
        "custom_domains": sum(1 for d in domains
                              if d.get("domain") not in PUBLIC_PROVIDERS),
        "public_providers": sum(1 for d in domains
                                if d.get("domain") in PUBLIC_PROVIDERS),
        "attachments": len(attachments),
        "attachment_bytes": attachment_bytes,
        "first": (min(dates) if dates else None),
        "latest": (max(dates) if dates else None),
    }
    date_fmt = "%Y-%m-%d %H:%M"
    if style.fa:
        stats["first"] = jalali_text(stats["first"].strftime(date_fmt)) if stats["first"] else "—"
        stats["latest"] = jalali_text(stats["latest"].strftime(date_fmt)) if stats["latest"] else "—"

    def folder_names(message):
        names = []
        for key in ("folder_display", "folder_imap"):
            for name in str(message.get(key) or "").split(","):
                name = name.strip()
                if not name:
                    continue
                shown = folder_labels.get(name, name)
                if shown not in names:
                    names.append(shown)
        return ", ".join(names)

    ctx = {
        "purpose": purpose,
        "scope_cfg": scope_cfg,
        "stats": stats,
        "months": months,
        "top_contacts": top_contacts,
        "top_domains": top_domains,
        "account_label": (account or {}).get("display_name")
                         or (account or {}).get("email") or "",
        "scope_label": scope_description(scope_cfg, locale, account),
        "generated": (jalali_text(dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                      if style.fa else
                      dt.datetime.now().strftime("%Y-%m-%d %H:%M")),
        "messages": messages,
        "contacts": contacts,
        "domains": domains,
        "attachments": attachments,
        "content": content,
        "columns": columns,
        "recipient_lists": recipient_lists,
        "attachment_counts": attachment_counts,
        "attachment_names": attachment_names,
        "folder_names": folder_names,
        "notes": [],
        "row_counts": "",
    }

    notes = [t.get("noteProvider", ""),
             t.get("noteDraft", ""),
             t.get("duplicateNote", "")]
    if content.get("all_contacts"):
        notes.append(t.get("noteDirectory", ""))
    if content.get("body"):
        notes.append(t.get("noteBody", ""))
    ctx["notes"] = notes
    ctx["row_counts"] = (
        f"{t.get('sheetMessages', 'Messages')}: {stats['total']} • "
        f"{t.get('sheetContacts', 'Contacts')}: {stats['contacts']} • "
        f"{t.get('sheetDomains', 'Email Domains')}: {stats['domains']} • "
        f"{t.get('sheetAttachments', 'Attachments')}: {stats['attachments']}")

    wb = Workbook()
    wb.remove(wb.active)

    if content.get("summary"):
        _write_summary(wb, style, ctx, bool(content.get("charts", True)))
    if content.get("messages"):
        _write_messages(wb, style, ctx)
    if content.get("contacts"):
        _write_contacts(wb, style, ctx)
    if content.get("domains"):
        _write_domains(wb, style, ctx)
    if content.get("attachments"):
        _write_attachments(wb, style, ctx)
    _write_info(wb, style, ctx)

    if stats["total"] == 0:
        # Nothing matched: make the empty scope explicit instead of leaving
        # a set of blank worksheets.
        ws = wb.create_sheet(t.get("noData", "No data")[:31])
        ws.sheet_view.rightToLeft = style.rtl
        cell = ws.cell(row=1, column=1, value=t.get("noData", "No data"))
        cell.font = style.title
        cell.alignment = Alignment(horizontal=style.text_align)

    wb.save(path)
    return {
        "path": path,
        "messages": stats["total"],
        "contacts": stats["contacts"],
        "domains": stats["domains"],
        "attachments": stats["attachments"],
        "incoming": stats["incoming"],
        "outgoing": stats["outgoing"],
        "drafts": stats["drafts"],
        "sheets": [ws.title for ws in wb.worksheets],
    }
