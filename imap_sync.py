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
import socket
import ssl
import threading
import time
import traceback
from imapclient import IMAPClient
from imapclient import exceptions as _imap_exceptions

import config
import imaptls
import storage

BATCH_SIZE = 100
LOG = logging.getLogger("maildaan.sync")


class SyncError(Exception):
    """A user-facing synchronization failure with a stable error key.

    Carrying a key (e.g. 'sync.authFailed') lets the frontend show a
    concise, translated message while the raw protocol text stays in the
    application log.
    """

    def __init__(self, message, key="sync.connectFailed", detail=None):
        super().__init__(message)
        self.key = key
        self.detail = detail


# Server responses that indicate a rejected login rather than a network or
# server problem. Matched case-insensitively against the exception message.
_AUTH_FAILURE_HINTS = (
    "authentication failed", "authenticationfailed", "authorization failed",
    "login failed", "invalid credentials", "auth exchange failed",
    "application-specific password", "web login required", "access denied",
    "operation not allowed", "account disabled", "no authentication",
)


def _is_auth_failure(exc) -> bool:
    if isinstance(exc, _imap_exceptions.LoginError):
        return True
    text = str(exc or "").lower()
    return any(hint in text for hint in _AUTH_FAILURE_HINTS)


def _classify_connect_failure(exc, host: str, port: int) -> SyncError:
    """Map a connection/login exception to a user-facing error class.

    Authentication failures are always separated from network and server
    problems so the UI never blames the account unless the server actually
    rejected the credentials. The exception's raw text is never shown to
    the user; only the safe host:port detail is.
    """
    where = f"{host}:{port}"
    if _is_auth_failure(exc):
        return SyncError("IMAP authentication failed", key="sync.authFailed")
    if isinstance(exc, socket.gaierror):
        return SyncError("IMAP server address not resolved",
                         key="sync.dnsFailed", detail=host)
    if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in str(exc or "").lower():
        return SyncError("IMAP connection timed out",
                         key="sync.timeout", detail=where)
    if isinstance(exc, ConnectionRefusedError):
        return SyncError("IMAP connection refused",
                         key="sync.refused", detail=where)
    if isinstance(exc, ssl.SSLError):
        return SyncError("IMAP TLS handshake failed",
                         key="sync.tlsError", detail=where)
    return SyncError("IMAP connection failed",
                     key="sync.connectFailed", detail=where)


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


# Content types treated as the readable message body (see _body_sections).
BODY_TYPES = ("text/plain", "text/html")


def _struct_text(value) -> str:
    """Lowercase an IMAPClient BODYSTRUCTURE atom (bytes or str)."""
    if isinstance(value, bytes):
        return value.decode("ascii", "replace").lower()
    if isinstance(value, str):
        return value.lower()
    return ""


def _param_lookup(params, key: str) -> str:
    """Read one parameter from an RFC 3501 body-fld-param list.

    IMAPClient flattens it to ``(key, value, key, value, ...)``; a list of
    explicit pairs is accepted as well.
    """
    if not isinstance(params, (list, tuple)):
        return ""
    items = list(params)
    if items and isinstance(items[0], (list, tuple)):  # pairs form
        for pair in items:
            if isinstance(pair, (list, tuple)) and len(pair) == 2 \
                    and _struct_text(pair[0]) == key:
                return _struct_text(pair[1])
        return ""
    for i in range(0, len(items) - 1, 2):
        if _struct_text(items[i]) == key:
            return _struct_text(items[i + 1])
    return ""


def _part_info(part):
    """(ctype, filename, disposition) of a BODYSTRUCTURE leaf part.

    IMAPClient exposes the RFC 3501 body structure fields as bytes. The
    media type lives at indexes 0/1, the parameters at index 2 (including a
    ``name`` on Content-Type) and the disposition at index 8, holding the
    tuple ("ATTACHMENT" ("FILENAME" ...)) for attachments. str values are
    accepted too so the classification is robust to either encoding.
    """
    ctype = ""
    if len(part) > 1:
        ctype = _struct_text(part[0]) + "/" + _struct_text(part[1])
    filename = disposition = ""
    params = part[2] if len(part) > 2 and isinstance(part[2], (list, tuple)) else ()
    filename = _param_lookup(params, "name")
    disp = part[8] if len(part) > 8 else None
    if isinstance(disp, (list, tuple)) and disp:
        disposition = _struct_text(disp[0])
        if not filename:
            filename = _param_lookup(
                disp[1] if len(disp) > 1 and isinstance(disp[1], (list, tuple)) else (),
                "filename")
    return ctype, filename, disposition


def _is_attachment_part(ctype: str, filename: str, disposition: str) -> bool:
    """Whether a MIME part's payload must be left unfetched.

    Mirrors the attachment test in parse_message(), so body parts and
    attachments are classified identically everywhere. Note this catches
    text-based attachments too (a text/plain section carrying
    ``name=notes.txt`` or Content-Disposition: attachment).
    """
    if filename or disposition == "attachment":
        return True
    return (ctype.startswith("application/") or ctype.startswith("image/")
            or ctype.startswith("audio/") or ctype.startswith("video/")
            or "octet-stream" in ctype)


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

    def _folder_imap_name(self, folder_id: int) -> str:
        """Look up the raw IMAP folder name for a folder id."""
        if not self.db:
            return "INBOX"
        with self.db._lock:
            row = self.db.conn.execute(
                "SELECT imap_name FROM folders WHERE id=?", (folder_id,)).fetchone()
        return row["imap_name"] if row else "INBOX"

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
        try:
            client.login(self.username, self.password)
        except Exception as exc:
            # Keep the raw server text in the log only; the user gets a
            # classified, translated message. (The password is never part
            # of an imapclient login failure.)
            if _is_auth_failure(exc):
                LOG.warning("IMAP login rejected for %s: %s", self.host, exc)
                raise SyncError("IMAP authentication failed",
                                key="sync.authFailed") from exc
            LOG.error("IMAP login failed for %s: %s", self.host, exc)
            raise _classify_connect_failure(exc, self.host, self.port) from exc
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
            # A no-attachment fetch (BODYSTRUCTURE + BODY[HEADER] + the
            # numbered BODY[<section>] parts) carries no BODY[] entry. The
            # header block identifies the message: it backs the placeholder
            # EML and the content hash replaced later by completion.
            header = entry.get(b"BODY[HEADER]") or entry.get(b"BODY.PEEK[HEADER]")
            if header:
                return bytes(header)
            for value in entry.values():
                if isinstance(value, (bytes, bytearray)):
                    return bytes(value)
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
            # Classified failure: the user sees a concise translated
            # message; the technical cause stays in the log.
            failure = exc if isinstance(exc, SyncError) else \
                _classify_connect_failure(exc, self.host, self.port)
            LOG.error("IMAP connection failed for %s:%s: %s",
                      self.host, self.port, exc)
            self.db.finish_sync_log(log_id, "error", 0, 0, 1, time.time() - started)
            self.db.close()
            with self.state.lock:
                self.state.status = "error"
                self.state.last_error = str(failure)
                self.state.running = False
            return {"status": "error", "error": str(failure),
                    "error_key": failure.key, "error_detail": failure.detail,
                    "new_messages": 0, "deduped": 0, "errors": 1,
                    "folders_processed": 0, "elapsed": time.time() - started,
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
            self._run_error_key = "sync.failed"
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
            "error_key": getattr(self, "_run_error_key", None),
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
                    body_plans = None
                else:
                    # NOTE: BODY.PEEK[TEXT] is deliberately NOT requested. For a
                    # multipart message it resolves to the *first* MIME section,
                    # which is an attachment for many real-world messages. Instead
                    # BODYSTRUCTURE is read first and only the numbered sections
                    # that are actually readable body parts are fetched below.
                    fetched = client.fetch(batch, ["BODYSTRUCTURE", "BODY.PEEK[HEADER]"])
                    body_plans = self._fetch_body_sections(client, batch, fetched)
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
                            parsed = self._parse_partial(
                                fetched, uid, content_hash,
                                body_plans.get(uid, {}) if body_plans else {})
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

    def _fetch_body_sections(self, client, uids, fetched) -> dict:
        """Fetch the readable text sections named by BODYSTRUCTURE.

        Returns ``{uid: {section: content_type}}``. Messages sharing a layout
        are fetched with one command. Attachment sections are never requested,
        so their payloads stay on the server until a later completion pass.
        """
        plans, groups = {}, {}
        for uid in uids:
            entry = fetched.get(uid)
            bodystruct = entry.get(b"BODYSTRUCTURE") if isinstance(entry, dict) else None
            plan = dict(self._body_sections(bodystruct)) if bodystruct is not None else {}
            plans[uid] = plan
            groups.setdefault(tuple(sorted(plan)), []).append(uid)
        for sections, group in groups.items():
            if not sections:
                continue
            # Merge per message: dict.update() would drop the BODYSTRUCTURE
            # and BODY[HEADER] entries already fetched for these messages.
            fetched_parts = client.fetch(
                group, ["BODY.PEEK[%s]" % s for s in sections])
            for uid, entry in fetched_parts.items():
                if isinstance(entry, dict):
                    fetched.setdefault(uid, {}).update(entry)
        return plans

    def _body_sections(self, bodystruct) -> list:
        """Sections of the readable body parts of a message.

        Walks the BODYSTRUCTURE tree and returns ``(section, content_type)``
        pairs for the text parts a user would read, using the IMAP section
        numbering ("1", "2.1", ...). A non-multipart message has no numbered
        sections, so its single part is addressed with the plain ``TEXT``
        specifier (attachments cannot hide in a single-part message).
        """
        sections = []
        self._collect_body_sections(bodystruct, "", sections)
        return sections

    def _collect_body_sections(self, part, prefix, out):
        if not isinstance(part, (list, tuple)) or not part:
            return
        if isinstance(part[0], (list, tuple)):
            # Multipart container: recurse into the numbered child parts.
            for index, child in enumerate(part[0], start=1):
                child_prefix = ("%s.%d" % (prefix, index)) if prefix else str(index)
                self._collect_body_sections(child, child_prefix, out)
            return
        ctype, filename, disposition = _part_info(part)
        if ctype in BODY_TYPES and not _is_attachment_part(ctype, filename, disposition):
            out.append((prefix or "TEXT", ctype))

    def _parse_partial(self, fetched, uid: int, content_hash: str,
                       sections: dict = None) -> dict:
        """Parse message without attachment payloads.

        Headers come from BODY[HEADER]; the bodies come from the numbered
        BODY[<section>] parts selected by _fetch_body_sections(), so no
        attachment payload is ever read. Attachment metadata comes from
        BODYSTRUCTURE only.
        """
        entry = fetched.get(uid)
        if not entry or not isinstance(entry, dict):
            return {"content_hash": content_hash}
        bodystruct = entry.get(b"BODYSTRUCTURE") or entry.get("BODYSTRUCTURE")
        header_bytes = entry.get(b"BODY[HEADER]") or entry.get("BODY.PEEK[HEADER]")
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
        plain_parts, html_parts = [], []
        for section, ctype in (sections or {}).items():
            key = ("BODY[%s]" % section).encode("ascii", "replace")
            data = entry.get(key) or entry.get(
                b"BODY.PEEK[" + section.encode("ascii", "replace") + b"]")
            if not data:
                continue
            text = data.decode("utf-8", "replace")
            (html_parts if ctype == "text/html" else plain_parts).append(text)
        plain_body = "\n".join(p for p in plain_parts if p and p.strip())
        html_body = "\n".join(h for h in html_parts if h and h.strip())
        if not plain_body and html_body:
            plain_body = html_to_text(html_body)
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
            "html_body": html_body,
            "size": 0,  # size unknown without full message
            "eml_relpath": storage.eml_relpath_for(content_hash),
            "recipients": recipients,
            "attachments": attachments,
            "complete": False,
        }

    def _collect_attachment_meta(self, part, out, idx=None):
        """Recursively collect attachment metadata from BODYSTRUCTURE."""
        # part is a list/tuple from IMAPClient: [type, subtype, params, id,
        # desc, enc, size, md5, (disposition ...), ...]. Fields are bytes.
        if idx is None:
            idx = [0]
        if not isinstance(part, (list, tuple)):
            return
        if len(part) >= 2 and isinstance(part[0], (bytes, str)) \
                and isinstance(part[1], (bytes, str)):
            # leaf part: [type, subtype, params, id, desc, enc, size, ...]
            ctype, filename, disposition = _part_info(part)
            if _is_attachment_part(ctype, filename, disposition):
                size = part[6] if len(part) > 6 and isinstance(part[6], int) else 0
                safe = storage.safe_filename(filename or
                                             (ctype.split("/")[-1] or "attachment"))
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
