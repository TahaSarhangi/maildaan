"""MailDaan IMAP synchronization engine.

Downloads every accessible message from all selectable IMAP folders into the
local SQLite/EML archive. Uses UID + UIDVALIDITY checkpoints so interrupted
runs resume exactly where they stopped, and never advance a checkpoint past
a failed batch.
"""

import datetime as dt
import email
import email.policy
import email.utils
import hashlib
import html as _html
import logging
import os
import re
import ssl
import threading
import time
import traceback
from imapclient import IMAPClient

import config
import imaptls
import storage

BATCH_SIZE = 100
LOG = logging.getLogger("maildaan.sync")


def html_to_text(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"(?is)<(script|style|head).*?</\1>", "", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|table)>", "\n", text)
    text = re.sub(r"(?i)<td[^>]*>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = _html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _decode_header(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_message(raw: bytes, content_hash: str) -> dict:
    """Parse raw MIME bytes into an archive record (no files written here)."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    message_id = _decode_header(msg["Message-ID"])
    subject = _decode_header(msg["Subject"])

    from_list = email.utils.getaddresses([_decode_header(msg["From"])])
    sender_name, sender_email = ("", "")
    if from_list and from_list[0][1]:
        sender_name = from_list[0][0] or ""
        sender_email = from_list[0][1].lower()

    date_ts, date_utc = None, None
    raw_date = _decode_header(msg["Date"])
    if raw_date:
        try:
            parsed = email.utils.parsedate_to_datetime(raw_date)
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt.timezone.utc)
                date_utc = parsed.astimezone(dt.timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S")
                date_ts = int(parsed.timestamp())
        except (TypeError, ValueError):
            pass

    plain_parts, html_parts = [], []
    attachments = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if filename or disposition == "attachment" or ctype.startswith("application/") or \
                ctype.startswith("image/") or ctype.startswith("audio/") or \
                ctype.startswith("video/") or "octet-stream" in ctype:
            try:
                content = part.get_content()
            except Exception:
                content = part.get_payload(decode=True)
            if isinstance(content, str):
                content = content.encode("utf-8", "replace")
            if content is None:
                continue
            safe = storage.safe_filename(filename or
                                         (ctype.split("/")[-1] or "attachment"))
            idx = len(attachments)
            attachments.append({
                "filename": filename or safe,
                "filename_safe": f"{idx:03d}-{safe}",
                "size": len(content),
                "mime_type": ctype,
                "relpath": os.path.join("attachments", content_hash[:16],
                                        f"{idx:03d}-{safe}"),
                "content": content,
            })
            continue
        if ctype == "text/plain" and disposition != "attachment":
            try:
                plain_parts.append(part.get_content())
            except Exception:
                payload = part.get_payload(decode=True)
                if payload:
                    plain_parts.append(payload.decode("utf-8", "replace"))
        elif ctype == "text/html" and disposition != "attachment":
            try:
                html_parts.append(part.get_content())
            except Exception:
                payload = part.get_payload(decode=True)
                if payload:
                    html_parts.append(payload.decode("utf-8", "replace"))

    plain_body = "\n".join(p for p in plain_parts if p and p.strip())
    html_body = "\n".join(h for h in html_parts if h and h.strip())
    if not plain_body and html_body:
        plain_body = html_to_text(html_body)

    recipients = []
    for rtype, header in (("to", "To"), ("cc", "Cc"), ("bcc", "Bcc")):
        for name, addr in email.utils.getaddresses([_decode_header(msg[header])]):
            if addr:
                recipients.append({"type": rtype, "name": name or "",
                                   "email": addr.lower()})

    return {
        "content_hash": content_hash,
        "message_id": message_id,
        "date_utc": date_utc,
        "date_ts": date_ts,
        "sender_name": sender_name,
        "sender_email": sender_email,
        "subject": subject,
        "plain_body": plain_body,
        "html_body": html_body,
        "size": len(raw),
        "eml_relpath": storage.eml_relpath_for(content_hash),
        "recipients": recipients,
        "attachments": attachments,
    }


class SyncState:
    """Thread-safe snapshot of synchronization progress."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.cancelled = False
        self.status = "idle"          # idle | running | completed | incomplete | cancelled | error
        self.phase = ""               # human hint, English key for UI translation
        self.total_pending = 0
        self.total_processed = 0
        self.folders_total = 0
        self.folders_done = 0
        self.current_folder = ""
        self.folder_pending = 0
        self.folder_processed = 0
        self.start_time = 0.0
        self.last_error = ""
        self.error_folders = []
        self.connected = False
        self.account_id = ""
        self.account_email = ""

    def snapshot(self) -> dict:
        with self.lock:
            elapsed = max(time.time() - self.start_time, 0.001) if self.start_time else 0
            speed = (self.total_processed / elapsed) if elapsed else 0
            remaining = self.total_pending - self.total_processed
            eta = (remaining / speed) if speed > 0 else 0
            overall_pct = (100.0 * self.total_processed / self.total_pending
                           if self.total_pending else 0)
            folder_pct = (100.0 * self.folder_processed / self.folder_pending
                          if self.folder_pending else 0)
            folder_eta = 0
            if self.folder_pending and self.folder_processed:
                folder_speed = self.folder_processed / max(elapsed, 0.001)
                folder_eta = (self.folder_pending - self.folder_processed) / folder_speed \
                    if folder_speed else 0
            return {
                "running": self.running,
                "status": self.status,
                "phase": self.phase,
                "total_pending": self.total_pending,
                "total_processed": self.total_processed,
                "overall_percent": round(overall_pct, 1),
                "speed": round(speed, 1),
                "eta_seconds": round(eta),
                "folders_total": self.folders_total,
                "folders_done": self.folders_done,
                "current_folder": self.current_folder,
                "folder_pending": self.folder_pending,
                "folder_processed": self.folder_processed,
                "folder_percent": round(folder_pct, 1),
                "folder_eta_seconds": round(folder_eta),
                "elapsed_seconds": round(elapsed),
                "connected": self.connected,
                "account_id": self.account_id,
                "account_email": self.account_email,
                "last_error": self.last_error,
                "error_folders": list(self.error_folders),
            }


class SyncEngine:
    def __init__(self, host: str, port: int, username: str, password: str,
                 state: SyncState, cancel_event: threading.Event,
                 db_path: str = None, data_dir: str = None,
                 account_id: str = "", account_email: str = "",
                 account: dict = None, with_attachments: bool = True):
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.state = state
        self.cancel = cancel_event
        self.db = None
        self.db_path = db_path
        self.data_dir = data_dir
        self.with_attachments = with_attachments
        # Per-account TLS trust (custom CA / pinned certificate) so an
        # untrusted server can be handled per account without ever
        # weakening verification globally.
        self.trust = imaptls.load_trust(account) if account else {}
        with state.lock:
            state.account_id = account_id
            state.account_email = account_email

    # ------------------------------------------------------------------ utils

    def _folder_display(self, imap_name: str, delimiter) -> str:
        if isinstance(delimiter, bytes):
            # IMAPClient returns the folder delimiter as bytes.
            delimiter = delimiter.decode("ascii", "replace")
        if imap_name.upper() == "INBOX":
            return "Inbox"
        base = imap_name.split(delimiter)[-1] if delimiter else imap_name
        return base.strip() or imap_name

    def _connect(self) -> IMAPClient:
        """Connect over TLS and log in, using this account's trust settings."""
        try:
            client = imaptls.connect(self.host, self.port, self.trust, timeout=60)
        except (imaptls.CertificateRejected, ssl.SSLCertVerificationError) as exc:
            # The chain is not trusted for this account. Gather the
            # certificate the server is presenting now — on an inspection
            # socket that carries no IMAP traffic and no credentials — so
            # the user can review it and decide.
            pin_mismatch = isinstance(exc, imaptls.CertificateRejected)
            try:
                details = imaptls.inspect_certificate(self.host, self.port)
            except OSError:
                details = {"host": self.host, "port": self.port}
            details["pin_mismatch"] = pin_mismatch
            raise imaptls.CertificateRejected(str(exc), pin_mismatch, details) \
                from exc
        client.login(self.username, self.password)
        return client

    def _extract_raw(self, fetch_result, uid):
        entry = fetch_result.get(uid)
        if entry is None:
            return None
        if isinstance(entry, tuple):
            payload = entry[1]
            if isinstance(payload, dict):
                for key in (b"BODY[]", b"RFC822"):
                    if key in payload:
                        return payload[key]
                for key, value in payload.items():
                    if isinstance(value, (bytes, bytearray)):
                        return bytes(value)
            if isinstance(payload, (bytes, bytearray)):
                return bytes(payload)
            return None
        if isinstance(entry, list):
            for item in entry:
                raw = self._extract_raw({uid: item}, uid)
                if raw:
                    return raw
        if isinstance(entry, dict):
            for key in (b"BODY[]", b"RFC822"):
                if key in entry:
                    return entry[key]
        return None

    # ------------------------------------------------------------------- main

    def run(self) -> dict:
        self.db = storage.ArchiveDB(self.db_path) if self.db_path \
            else storage.ArchiveDB()
        log_id = self.db.start_sync_log()
        with self.state.lock:
            self.state.running = True
            self.state.status = "running"
            self.state.phase = "connecting"
        started = time.time()
        new_total, dedup_total, err_total = 0, 0, 0
        status = "completed"
        try:
            client = self._connect()
            with self.state.lock:
                self.state.connected = True
        except imaptls.CertificateRejected as exc:
            # The user must decide about this certificate: report its
            # details instead of a bare verification error.
            LOG.error("IMAP TLS trust failed for %s: %s", self.host, exc)
            self.db.finish_sync_log(log_id, "error", 0, 0, 1, time.time() - started)
            self.db.close()
            with self.state.lock:
                self.state.status = "error"
                self.state.last_error = str(exc)
                self.state.running = False
            return {"status": "error", "error": str(exc),
                    "tls_error": exc.details, "new_messages": 0,
                    "deduped": 0, "errors": 1, "folders_processed": 0,
                    "elapsed": time.time() - started,
                    "account_id": self.state.account_id,
                    "account_email": self.state.account_email}
        except Exception as exc:
            LOG.error("IMAP connection failed: %s", exc)
            self.db.finish_sync_log(log_id, "error", 0, 0, 1, time.time() - started)
            self.db.close()
            with self.state.lock:
                self.state.status = "error"
                self.state.last_error = str(exc)
                self.state.running = False
            return {"status": "error", "error": str(exc), "new_messages": 0,
                    "deduped": 0, "errors": 1, "folders_processed": 0,
                    "elapsed": time.time() - started,
                    "account_id": self.state.account_id,
                    "account_email": self.state.account_email}

        try:
            folders = self._discover_folders(client)
            # Phase 1: count pending work for accurate progress
            plan = []  # (folder_id, imap_name, display, uidvalidity, pending_uids)
            for imap_name, display in folders:
                if self.cancel.is_set():
                    break
                try:
                    info = client.select_folder(imap_name)
                    uidvalidity = info.get("UIDVALIDITY") or info.get(b"UIDVALIDITY") or 0
                    folder_id = self.db.upsert_folder(imap_name, display, int(uidvalidity))
                    checkpoint = self.db.get_folder_checkpoints()
                    last_uid = 0
                    for f in checkpoint:
                        if f["id"] == folder_id:
                            last_uid = f["last_completed_uid"]
                            break
                    # imapclient's search() takes one criteria argument:
                    # pass the criteria as a list so the UID range is not
                    # mistaken for the charset.
                    pending = [u for u in client.search(["UID", f"{last_uid + 1}:*"])
                               if isinstance(u, int) and u > last_uid]
                    if pending:
                        plan.append((folder_id, imap_name, display, pending))
                except Exception as exc:
                    LOG.warning("Cannot select folder %s: %s", imap_name, exc)
                    err_total += 1
                    with self.state.lock:
                        self.state.error_folders.append(display or imap_name)
                        self.state.last_error = str(exc)
                    continue

            with self.state.lock:
                self.state.folders_total = len(plan)
                self.state.total_pending = sum(len(p[3]) for p in plan)
                self.state.start_time = time.time()

            for folder_id, imap_name, display, pending in plan:
                if self.cancel.is_set():
                    status = "cancelled"
                    break
                with self.state.lock:
                    self.state.current_folder = display
                    self.state.folder_pending = len(pending)
                    self.state.folder_processed = 0
                    self.state.phase = "syncing"
                try:
                    client.select_folder(imap_name)
                except Exception as exc:
                    LOG.warning("Cannot reselect folder %s: %s", imap_name, exc)
                    err_total += 1
                    with self.state.lock:
                        self.state.error_folders.append(display)
                        self.state.last_error = str(exc)
                        self.state.folders_done += 1
                    continue
                folder_new, folder_dedup, folder_err = self._sync_folder(
                    client, folder_id, pending)
                new_total += folder_new
                dedup_total += folder_dedup
                err_total += folder_err
                with self.state.lock:
                    self.state.folders_done += 1
                if folder_err:
                    status = "incomplete"
                    with self.state.lock:
                        self.state.error_folders.append(display)
            if self.cancel.is_set():
                status = "cancelled"
        except Exception as exc:
            LOG.error("Synchronization failed: %s\n%s", exc, traceback.format_exc())
            status = "error"
            err_total += 1
            with self.state.lock:
                self.state.last_error = str(exc)
        finally:
            try:
                client.logout()
            except Exception:
                pass
            elapsed = time.time() - started
            self.db.finish_sync_log(log_id, status, new_total, dedup_total,
                                    err_total, elapsed)
            stats = self.db.archive_stats()
            self.db.close()

        with self.state.lock:
            self.state.status = status
            self.state.running = False
            self.state.phase = "done"

        return {
            "status": status,
            "account_id": self.state.account_id,
            "account_email": self.state.account_email,
            "folders_processed": len(plan) if 'plan' in locals() else 0,
            "new_messages": new_total,
            "deduped": dedup_total,
            "errors": err_total,
            "elapsed": elapsed,
            "total_messages": stats.get("messages", 0),
            "total_contacts": stats.get("contacts", 0),
            "total_attachments": stats.get("attachments", 0),
            "total_folders": stats.get("folders", 0),
            "error_folders": list(self.state.error_folders),
            "last_error": self.state.last_error,
        }

    def _discover_folders(self, client):
        try:
            listing = client.list_folders()
        except Exception:
            listing = []
        folders = []
        for flags, delimiter, name in listing:
            if any(b"Noselect" in (f if isinstance(f, bytes) else f.encode())
                   for f in flags):
                continue
            folders.append((name, self._folder_display(name, delimiter or "/")))
        if not folders:
            folders.append(("INBOX", "Inbox"))
        # deterministic order, Inbox first
        folders.sort(key=lambda x: (0 if x[0].upper() == "INBOX" else 1, x[0]))
        return folders

    def _sync_folder(self, client, folder_id: int, pending: list):
        new_count = dedup_count = err_count = 0
        for i in range(0, len(pending), BATCH_SIZE):
            if self.cancel.is_set():
                break
            batch = pending[i:i + BATCH_SIZE]
            try:
                if self.with_attachments:
                    fetched = client.fetch(batch, ["BODY.PEEK[]"])
                else:
                    fetched = client.fetch(batch, ["BODYSTRUCTURE", "BODY.PEEK[HEADER]", "BODY.PEEK[TEXT]"])
            except Exception as exc:
                LOG.warning("Fetch failed in folder %s: %s", folder_id, exc)
                err_count += len(batch)
                with self.state.lock:
                    self.state.last_error = str(exc)
                self.db.set_folder_error(folder_id, True)
                break  # keep checkpoint at last good position
            batch_ok = True
            for uid in batch:
                try:
                    raw = self._extract_raw(fetched, uid)
                    if not raw:
                        err_count += 1
                        batch_ok = False
                        continue
                    content_hash = hashlib.sha256(raw).hexdigest()
                    if self.db.message_hash_exists(content_hash):
                        self.db.insert_message(
                            {"content_hash": content_hash}, folder_id, uid)
                        dedup_count += 1
                    else:
                        if self.with_attachments:
                            parsed = parse_message(raw, content_hash)
                            self._write_files(raw, parsed)
                            self.db.insert_message(parsed, folder_id, uid)
                        else:
                            parsed = self._parse_partial(fetched, uid, content_hash)
                            self._write_files(raw, parsed)
                            self.db.insert_message(parsed, folder_id, uid)
                        new_count += 1
                except Exception as exc:
                    LOG.warning("Failed to process UID %s: %s", uid, exc)
                    err_count += 1
                    batch_ok = False
                with self.state.lock:
                    self.state.total_processed += 1
                    self.state.folder_processed += 1
                if self.cancel.is_set():
                    batch_ok = False
                    break
            # Advance checkpoint ONLY when the whole batch succeeded.
            if batch_ok and not self.cancel.is_set():
                self.db.advance_checkpoint(folder_id, max(batch))
                self.db.set_folder_error(folder_id, False)
            else:
                self.db.set_folder_error(folder_id, True)
                break
        return new_count, dedup_count, err_count

    def _write_files(self, raw: bytes, parsed: dict) -> None:
        data_dir = self.data_dir or config.get_data_dir()
        eml_path = os.path.join(data_dir, parsed["eml_relpath"])
        os.makedirs(os.path.dirname(eml_path), exist_ok=True)
        if not os.path.exists(eml_path):
            with open(eml_path, "wb") as fh:
                fh.write(raw)
        for att in parsed["attachments"]:
            content = att.pop("content", None)
            if content is None:
                continue
            att_path = os.path.join(data_dir, att["relpath"])
            os.makedirs(os.path.dirname(att_path), exist_ok=True)
            if not os.path.exists(att_path):
                with open(att_path, "wb") as fh:
                    fh.write(content)

    def _parse_partial(self, fetched, uid: int, content_hash: str) -> dict:
        """Parse message without attachment payloads using BODYSTRUCTURE + selective parts."""
        entry = fetched.get(uid)
        if not entry or not isinstance(entry, dict):
            return {"content_hash": content_hash}
        bodystruct = entry.get(b"BODYSTRUCTURE") or entry.get("BODYSTRUCTURE")
        header_bytes = entry.get(b"BODY.PEEK[HEADER]") or entry.get("BODY.PEEK[HEADER]")
        text_bytes = entry.get(b"BODY.PEEK[TEXT]") or entry.get("BODY.PEEK[TEXT]")
        # Parse headers
        import email, email.utils, datetime as dt
        msg = email.message_from_bytes(header_bytes or b"", policy=email.policy.default)
        message_id = (msg.get("Message-ID") or "").strip()
        subject = (msg.get("Subject") or "").strip()
        from_list = email.utils.getaddresses([(msg.get("From") or "").strip()])
        sender_name, sender_email = ("", "")
        if from_list and from_list[0][1]:
            sender_name = from_list[0][0] or ""
            sender_email = from_list[0][1].lower()
        date_ts, date_utc = None, None
        raw_date = (msg.get("Date") or "").strip()
        if raw_date:
            try:
                parsed = email.utils.parsedate_to_datetime(raw_date)
                if parsed is not None:
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=dt.timezone.utc)
                    date_utc = parsed.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    date_ts = int(parsed.timestamp())
            except (TypeError, ValueError):
                pass
        plain_body = ""
        if text_bytes:
            try:
                plain_body = text_bytes.decode("utf-8", "replace")
            except Exception:
                plain_body = ""
        # Recipients
        recipients = []
        for rtype, header in (("to", "To"), ("cc", "Cc"), ("bcc", "Bcc")):
            for name, addr in email.utils.getaddresses([(msg.get(header) or "").strip()]):
                if addr:
                    recipients.append({"type": rtype, "name": name or "", "email": addr.lower()})
        # Attachment metadata from BODYSTRUCTURE
        attachments = []
        if bodystruct:
            self._collect_attachment_meta(bodystruct, attachments)
        return {
            "content_hash": content_hash,
            "message_id": message_id,
            "date_utc": date_utc,
            "date_ts": date_ts,
            "sender_name": sender_name,
            "sender_email": sender_email,
            "subject": subject,
            "plain_body": plain_body,
            "html_body": "",
            "size": 0,  # size unknown without full message
            "eml_relpath": storage.eml_relpath_for(content_hash),
            "recipients": recipients,
            "attachments": attachments,
            "complete": False,
        }

    def _collect_attachment_meta(self, part, out, idx=[0]):
        """Recursively collect attachment metadata from BODYSTRUCTURE."""
        # part is a list/tuple from IMAPClient: [type, subtype, params, id, desc, enc, size, ...]
        if not isinstance(part, (list, tuple)):
            return
        if len(part) >= 2 and isinstance(part[0], str) and isinstance(part[1], str):
            # leaf part: [type, subtype, params, ...]
            ctype = (part[0] + "/" + part[1]).lower()
            params = part[2] if len(part) > 2 and isinstance(part[2], list) else []
            disposition = "attachment"
            filename = None
            for p in params:
                if isinstance(p, (list, tuple)) and len(p) == 2:
                    if p[0].lower() == "name":
                        filename = p[1]
                    if p[0].lower() == "content-disposition":
                        disposition = p[1].lower()
            if filename or disposition == "attachment" or ctype.startswith("application/") or \
                    ctype.startswith("image/") or ctype.startswith("audio/") or \
                    ctype.startswith("video/") or "octet-stream" in ctype:
                size = part[6] if len(part) > 6 and isinstance(part[6], int) else 0
                safe = storage.safe_filename(filename or (ctype.split("/")[-1] or "attachment"))
                idx[0] += 1
                out.append({
                    "filename": filename or safe,
                    "filename_safe": f"{idx[0]:03d}-{safe}",
                    "size": size,
                    "mime_type": ctype,
                    "relpath": "",
                })
            return
        for sub in part:
            self._collect_attachment_meta(sub, out, idx)
