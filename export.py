"""Optional Excel report generation for MailDaan (openpyxl).

Excel files are only ever created when the user explicitly requests an export.
"""

import datetime as dt
import json
import os
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import config

# Characters Excel forbids inside text cells: C0 except TAB/LF/CR, plus DEL.
_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
EXCEL_CELL_LIMIT = 32767


def sanitize_cell(value, limit=True):
    """Clean a value for safe Excel writing without altering source data."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    text = _ILLEGAL_RE.sub("", text)
    # collapse stray lone CR
    text = text.replace("\r", "")
    if limit and len(text) > EXCEL_CELL_LIMIT:
        text = text[:EXCEL_CELL_LIMIT - 12] + "\n[…]"
    # never let a value be interpreted as a formula
    if text[:1] in ("=", "+", "-", "@"):
        text = "​" + text
    return text


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


def _parse_date(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return dt.datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


_HEADER_FILL = PatternFill("solid", start_color="FF1F4E79")
_HEADER_FONT = Font(bold=True, color="FFFFFFFF", size=11)


def _style_sheet(ws, widths, rtl):
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(widths))}{ws.max_row}"
    ws.sheet_view.rightToLeft = rtl


def _write_row(ws, row_idx, values, wrap_cols=(), date_cols=()):
    for col_idx, value in enumerate(values, start=1):
        cell = ws.cell(row=row_idx, column=col_idx)
        if col_idx in date_cols:
            parsed = _parse_date(value)
            if parsed is not None:
                cell.value = parsed
                cell.number_format = "yyyy-mm-dd hh:mm"
                cell.alignment = Alignment(horizontal="left")
                continue
        cell.value = sanitize_cell(value)
        if col_idx in wrap_cols:
            cell.alignment = Alignment(wrap_text=True, vertical="top")


def generate_workbook(db, filters, sort, lang, path):
    """Create the Excel report at `path`. Returns a summary dict."""
    t = load_locale(lang).get("excel", {})
    rtl = (lang == "fa")

    wb = Workbook()

    # ------------------------------------------------------------- messages
    messages = db.export_messages(filters, sort)
    msg_ids = [m["id"] for m in messages]
    recipients = db.export_recipients_for(msg_ids)
    attachments = db.export_attachments_for(msg_ids)
    rec_by_msg = {}
    for r in recipients:
        rec_by_msg.setdefault(r["message_id"], {"to": [], "cc": [], "bcc": []}) \
            [r["rtype"]].append(f"{r['name'] or ''} <{r['email']}>".strip())
    att_by_msg = {}
    total_att_size = 0
    for a in attachments:
        att_by_msg.setdefault(a["message_id"], []).append(a["filename"])
        total_att_size += a["size"] or 0

    ws = wb.active
    ws.title = t.get("sheetMessages", "Messages")
    ws.append([
        t.get("colDate", "Date"), t.get("colSenderName", "Sender name"),
        t.get("colSenderEmail", "Sender email"), t.get("colRecipients", "Recipients"),
        t.get("colCc", "CC"), t.get("colBcc", "BCC"), t.get("colSubject", "Subject"),
        t.get("colBody", "Body"), t.get("colFolder", "Folder"),
        t.get("colAttachments", "Attachments"), t.get("colEml", "Original EML"),
    ])
    for i, m in enumerate(messages, start=2):
        rec = rec_by_msg.get(m["id"], {"to": [], "cc": [], "bcc": []})
        _write_row(ws, i, [
            m.get("date_utc") or "",
            m.get("sender_name") or "",
            m.get("sender_email") or "",
            "; ".join(x for x in rec["to"] if x),
            "; ".join(x for x in rec["cc"] if x),
            "; ".join(x for x in rec["bcc"] if x),
            m.get("subject") or "",
            m.get("plain_body") or "",
            m.get("folders") or "",
            ", ".join(att_by_msg.get(m["id"], [])),
            m.get("eml_relpath") or "",
        ], wrap_cols=(8,), date_cols=(1,))
    _style_sheet(ws, [18, 24, 28, 34, 28, 24, 40, 60, 22, 30, 40], rtl)

    # -------------------------------------------------------------- contacts
    contacts = db.export_contacts(msg_ids if filters else None)
    ws2 = wb.create_sheet(t.get("sheetContacts", "Contacts"))
    ws2.append([
        t.get("colContactName", "Contact name"), t.get("colEmail", "Email"),
        t.get("colDomain", "Domain"), t.get("colFirst", "First communication"),
        t.get("colLast", "Last communication"), t.get("colMessages", "Messages"),
        t.get("colSent", "Sent"), t.get("colReceived", "Received"),
    ])
    for i, c in enumerate(contacts, start=2):
        _write_row(ws2, i, [
            c.get("display_name") or "", c.get("email_norm") or "",
            c.get("domain") or "", c.get("first_seen") or "", c.get("last_seen") or "",
            (c.get("msg_count") if "msg_count" in c else
             (c.get("sent_count") or 0) + (c.get("received_count") or 0)),
            c.get("sent_count", 0), c.get("received_count", 0),
        ], date_cols=(4, 5))
    _style_sheet(ws2, [28, 34, 24, 20, 20, 12, 10, 12], rtl)

    # ---------------------------------------------------------- organizations
    orgs = db.export_organizations(msg_ids if filters else None)
    ws3 = wb.create_sheet(t.get("sheetOrganizations", "Organizations"))
    ws3.append([
        t.get("colDomain", "Email domain"), t.get("colRelatedContacts", "Related contacts"),
        t.get("colMessages", "Messages"), t.get("colFirst", "First communication"),
        t.get("colLast", "Last communication"),
    ])
    for i, o in enumerate(orgs, start=2):
        _write_row(ws3, i, [
            o.get("domain") or "", o.get("contact_count", 0),
            o.get("message_count", 0), o.get("first_seen") or "",
            o.get("last_seen") or "",
        ], date_cols=(4, 5))
    _style_sheet(ws3, [30, 18, 12, 20, 20], rtl)

    wb.save(path)
    return {
        "messages": len(messages),
        "contacts": len(contacts),
        "organizations": len(orgs),
        "path": path,
        "attachment_bytes": total_att_size,
    }
