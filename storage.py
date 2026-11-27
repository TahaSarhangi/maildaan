"""MailDaan local SQLite archive storage."""

import hashlib
import os
import re
import sqlite3
import threading
import time
import datetime as _dt

import config
import search

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY,
    imap_name TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    uidvalidity INTEGER NOT NULL DEFAULT 0,
    last_completed_uid INTEGER NOT NULL DEFAULT 0,
    known_count INTEGER NOT NULL DEFAULT 0,
    has_error INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    content_hash TEXT UNIQUE NOT NULL,
    message_id TEXT,
    date_utc TEXT,
    date_ts INTEGER,
    sender_name TEXT,
    sender_email TEXT,
    subject TEXT,
    plain_body TEXT,
    html_body TEXT,
    search_norm TEXT,
    search_all TEXT,
    size INTEGER,
    eml_relpath TEXT,
    created_at TEXT,
    complete INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS message_folders (
    message_id INTEGER NOT NULL,
    folder_id INTEGER NOT NULL,
    uid INTEGER NOT NULL,
    PRIMARY KEY (message_id, folder_id)
);
CREATE TABLE IF NOT EXISTS recipients (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL,
    rtype TEXT NOT NULL,
    name TEXT,
    email TEXT
);
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL,
    filename TEXT,
    filename_safe TEXT,
    size INTEGER,
    mime_type TEXT,
    relpath TEXT
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    email_norm TEXT UNIQUE NOT NULL,
    display_name TEXT,
    domain TEXT,
    first_seen TEXT,
    last_seen TEXT,
    sent_count INTEGER NOT NULL DEFAULT 0,
    received_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS organizations (
    id INTEGER PRIMARY KEY,
    domain TEXT UNIQUE NOT NULL,
    first_seen TEXT,
    last_seen TEXT,
    contact_count INTEGER NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    new_messages INTEGER DEFAULT 0,
    deduped INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    elapsed_seconds REAL DEFAULT 0
);
-- Locally deleted messages, remembered per folder so an ordinary IMAP
-- synchronization never silently restores a message the user removed from
-- the local archive. Scoped by UIDVALIDITY: after a genuine change the
-- tombstones no longer match and every server message is eligible again.
CREATE TABLE IF NOT EXISTS deleted_uids (
    folder_id INTEGER NOT NULL,
    uid INTEGER NOT NULL,
    uidvalidity INTEGER NOT NULL,
    deleted_at TEXT,
    PRIMARY KEY (folder_id, uid)
);
-- Per-message failures of the latest run. UIDs recorded here stay eligible
-- for the next synchronization even after the folder checkpoint has moved
-- past them, so a checkpoint never falsely marks a failed item as done.
CREATE TABLE IF NOT EXISTS sync_failures (
    folder_id INTEGER NOT NULL,
    uid INTEGER NOT NULL,
    uidvalidity INTEGER NOT NULL,
    reason TEXT,
    attempts INTEGER NOT NULL DEFAULT 1,
    last_attempt TEXT,
    PRIMARY KEY (folder_id, uid)
);
CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(date_ts);
CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_email);
CREATE INDEX IF NOT EXISTS idx_recipients_email ON recipients(email);
CREATE INDEX IF NOT EXISTS idx_recipients_msg ON recipients(message_id);
CREATE INDEX IF NOT EXISTS idx_msg_folders_folder ON message_folders(folder_id);
CREATE INDEX IF NOT EXISTS idx_msg_folders_msg ON message_folders(message_id);
CREATE INDEX IF NOT EXISTS idx_attachments_msg ON attachments(message_id);
CREATE INDEX IF NOT EXISTS idx_contacts_domain ON contacts(domain);
"""

# FTS5 full-text index over the normalized search representation of every
# message (additive: existing 1.0/1.1.0 archives are migrated in place).
# search_fts stores its own content, so triggers keep it in sync with plain
# rowid deletes (the special 'delete' INSERT form only applies to
# contentless/external-content tables and would raise "SQL logic error").
FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    norm, tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
    INSERT INTO search_fts(rowid, norm) VALUES (new.id, new.search_all);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
    DELETE FROM search_fts WHERE rowid = old.id;
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages BEGIN
    DELETE FROM search_fts WHERE rowid = old.id;
    INSERT INTO search_fts(rowid, norm) VALUES (new.id, new.search_all);
END;
"""

_PERSIAN_MAP = str.maketrans({
    "ي": "ی", "ى": "ی", "ك": "ک",
    "أ": "ا", "إ": "ا", "ؤ": "و", "ئ": "ی",
})


def normalize_text(value: str) -> str:
    """Normalize text for search (Persian variants + case folding).

    Control characters are removed because a NUL byte inside a stored value
    breaks SQLite LIKE matching for the entire string.
    """
    if not value:
        return ""
    text = value.translate(_PERSIAN_MAP).casefold()
    return _CONTROL_RE.sub("", text)


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def safe_filename(name: str, fallback: str = "file") -> str:
    """Strip characters that are invalid in Windows paths."""
    if not name:
        return fallback
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:180] or fallback


def attachment_physical_name(index: int, content_hash: str, filename: str = "",
                             mime_type: str = "") -> str:
    """Short, unique, filesystem-safe physical name for one attachment file.

    The original readable name is kept in the archive metadata; only the
    stored payload file uses this compact deterministic name. This keeps
    every attachment path far below the Windows MAX_PATH limit even when a
    sender handed over a very long, percent-encoded or non-Latin filename,
    and prevents collisions between attachments.
    """
    ext = os.path.splitext(filename or "")[1].lstrip(".")
    if not ext and mime_type:
        ext = mime_type.split("/")[-1] or ""
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:12].lower()
    return "%03d-%s%s" % (index, content_hash[16:28],
                          ("." + ext) if ext else "")


# ArchiveDB setup (schema, migration, index build) is serialized per database
# file: the frontend can open the same archive concurrently from parallel
# bridge calls, and SQLite refuses a journal-mode change while another
# connection is setting the database up for the first time.
_setup_locks_guard = threading.Lock()
_setup_locks = {}


def _setup_lock_for(path: str) -> threading.Lock:
    with _setup_locks_guard:
        lock = _setup_locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _setup_locks[path] = lock
        return lock


class ArchiveDB:
    """Thin SQLite wrapper. One instance per thread."""

    def __init__(self, path: str = None):
        self.path = path or config.get_legacy_db_path()
        self._lock = threading.Lock()
        setup_lock = _setup_lock_for(self.path)
        with setup_lock:
            self.conn = sqlite3.connect(self.path, check_same_thread=False,
                                        timeout=30.0)
            self.conn.row_factory = sqlite3.Row
            try:
                self.conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass  # already in WAL mode, or another connection owns it
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            with self._lock:
                self.conn.executescript(SCHEMA)
                self.conn.commit()
            self._fts_ok = self._setup_search_index()

    def _setup_search_index(self) -> bool:
        """Additive 1.1.1 search migration: search_all column + FTS5 index.

        Existing archives are upgraded in place; rows missing the normalized
        search text are backfilled in batches and the FTS index is
        synchronized once. Never deletes or rewrites user message data.
        """
        with self._lock:
            try:
                cols = {row["name"] for row in
                        self.conn.execute("PRAGMA table_info(messages)")}
                if "search_all" not in cols:
                    self.conn.execute(
                        "ALTER TABLE messages ADD COLUMN search_all TEXT")
                if "complete" not in cols:
                    self.conn.execute(
                        "ALTER TABLE messages ADD COLUMN complete INTEGER NOT NULL DEFAULT 1")
                fts_ok = True
                try:
                    self.conn.executescript(FTS_SQL)
                except sqlite3.OperationalError:
                    fts_ok = False  # SQLite build without FTS5: LIKE still works
                self.conn.commit()
                if fts_ok:
                    (missing,) = self.conn.execute(
                        "SELECT COUNT(*) FROM messages WHERE search_all IS NULL"
                    ).fetchone()
                    if missing:
                        self._backfill_search_all()
                    (n_msgs,) = self.conn.execute(
                        "SELECT COUNT(*) FROM messages").fetchone()
                    (n_fts,) = self.conn.execute(
                        "SELECT COUNT(*) FROM search_fts").fetchone()
                    if n_fts != n_msgs:
                        self.conn.execute("DELETE FROM search_fts")
                        self.conn.execute(
                            "INSERT INTO search_fts(rowid, norm) "
                            "SELECT id, search_all FROM messages "
                            "WHERE search_all IS NOT NULL")
                    self.conn.commit()
                return fts_ok
            except sqlite3.OperationalError:
                return False

    def _backfill_search_all(self) -> None:
        """Recompute the normalized search text for pre-1.1.1 rows."""
        while True:
            rows = self.conn.execute(
                "SELECT id, sender_name, sender_email, subject, plain_body "
                "FROM messages WHERE search_all IS NULL ORDER BY id LIMIT 500"
            ).fetchall()
            if not rows:
                break
            ids = [row["id"] for row in rows]
            placeholders = ",".join("?" * len(ids))
            recs = self.conn.execute(
                f"SELECT message_id, name, email FROM recipients "
                f"WHERE message_id IN ({placeholders})", ids).fetchall()
            by_msg = {}
            for rec in recs:
                by_msg.setdefault(rec["message_id"], []).append(
                    {"name": rec["name"], "email": rec["email"]})
            for row in rows:
                text = search.message_search_text({
                    "sender_name": row["sender_name"],
                    "sender_email": row["sender_email"],
                    "subject": row["subject"],
                    "plain_body": row["plain_body"],
                    "recipients": by_msg.get(row["id"], []),
                })
                self.conn.execute(
                    "UPDATE messages SET search_all=? WHERE id=?",
                    (text, row["id"]))
            self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.commit()
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    # ---------------------------------------------------------------- folders

    def upsert_folder(self, imap_name: str, display_name: str, uidvalidity: int):
        with self._lock:
            row = self.conn.execute(
                "SELECT id, uidvalidity, last_completed_uid FROM folders WHERE imap_name=?",
                (imap_name,)).fetchone()
            if row:
                if row["uidvalidity"] != uidvalidity:
                    # UIDVALIDITY changed: reset checkpoint, keep archived rows.
                    self.conn.execute(
                        "UPDATE folders SET uidvalidity=?, last_completed_uid=0, has_error=0 "
                        "WHERE id=?", (uidvalidity, row["id"]))
                    # Tombstones and failure records of the old UIDVALIDITY no
                    # longer identify any message: drop them so they can never
                    # suppress or resurrect unrelated UIDs of the new epoch.
                    self.conn.execute(
                        "DELETE FROM deleted_uids WHERE folder_id=? AND uidvalidity<>?",
                        (row["id"], uidvalidity))
                    self.conn.execute(
                        "DELETE FROM sync_failures WHERE folder_id=? AND uidvalidity<>?",
                        (row["id"], uidvalidity))
                return row["id"]
            cur = self.conn.execute(
                "INSERT INTO folders (imap_name, display_name, uidvalidity) VALUES (?,?,?)",
                (imap_name, display_name, uidvalidity))
            self.conn.commit()
            return cur.lastrowid

    def get_folder_checkpoints(self):
        with self._lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT id, imap_name, display_name, uidvalidity, last_completed_uid, "
                "known_count, has_error FROM folders").fetchall()]

    def reset_folder_checkpoint(self, folder_id: int):
        with self._lock:
            self.conn.execute(
                "UPDATE folders SET last_completed_uid=0 WHERE id=?", (folder_id,))
            self.conn.commit()

    def set_folder_error(self, folder_id: int, has_error: bool):
        with self._lock:
            self.conn.execute("UPDATE folders SET has_error=? WHERE id=?",
                              (1 if has_error else 0, folder_id))
            self.conn.commit()

    # ------------------------------------------------- tombstones & failures

    def _folder_uidvalidity(self, folder_id: int) -> int:
        row = self.conn.execute(
            "SELECT uidvalidity FROM folders WHERE id=?", (folder_id,)).fetchone()
        return int(row["uidvalidity"]) if row else 0

    def mark_uid_deleted(self, folder_id: int, uid: int) -> None:
        """Remember an intentionally deleted message so a later ordinary
        synchronization does not silently download it again. The record is
        scoped to the folder's current UIDVALIDITY."""
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO deleted_uids (folder_id, uid, uidvalidity, deleted_at) "
                "SELECT ?, ?, uidvalidity, ? FROM folders WHERE id=?",
                (folder_id, uid, utc_now_iso(), folder_id))
            self.conn.commit()

    def deleted_uids_for(self, folder_id: int) -> set:
        """UIDs to skip during synchronization (intentional local deletions
        that belong to the folder's current UIDVALIDITY epoch)."""
        with self._lock:
            if self.conn.execute(
                    "SELECT 1 FROM deleted_uids WHERE folder_id=? LIMIT 1",
                    (folder_id,)).fetchone() is None:
                return set()
            uv = self._folder_uidvalidity(folder_id)
            return {r["uid"] for r in self.conn.execute(
                "SELECT uid FROM deleted_uids WHERE folder_id=? AND uidvalidity=?",
                (folder_id, uv))}

    def record_sync_failure(self, folder_id: int, uid: int, uidvalidity: int,
                            reason: str) -> None:
        """Record (or re-count) a per-message failure.

        Failed UIDs stay eligible for the next synchronization even when the
        folder checkpoint has already advanced past them; this table is the
        counterpart that keeps them visible."""
        with self._lock:
            self.conn.execute(
                "INSERT INTO sync_failures (folder_id, uid, uidvalidity, reason, "
                "  attempts, last_attempt) VALUES (?,?,?,?,1,?) "
                "ON CONFLICT(folder_id, uid) DO UPDATE SET "
                "  uidvalidity=excluded.uidvalidity, reason=excluded.reason, "
                "  attempts=attempts+1, last_attempt=excluded.last_attempt",
                (folder_id, uid, uidvalidity, reason, utc_now_iso()))
            self.conn.commit()

    def clear_sync_failure(self, folder_id: int, uid: int) -> None:
        with self._lock:
            self.conn.execute(
                "DELETE FROM sync_failures WHERE folder_id=? AND uid=?",
                (folder_id, uid))
            self.conn.commit()

    def sync_failures_for(self, folder_id: int) -> set:
        """UIDs that failed during an earlier run and must be retried
        (restricted to the folder's current UIDVALIDITY epoch)."""
        with self._lock:
            uv = self._folder_uidvalidity(folder_id)
            return {r["uid"] for r in self.conn.execute(
                "SELECT uid FROM sync_failures WHERE folder_id=? AND uidvalidity=?",
                (folder_id, uv))}

    def failed_uid_count(self) -> int:
        with self._lock:
            return self.conn.execute(
                "SELECT COUNT(*) FROM sync_failures").fetchone()[0]

    # ------------------------------------------------------ local deletion

    def delete_messages(self, message_ids, folder_id: int = None) -> dict:
        """Delete message rows from the local archive and report their files.

        Tombstones are recorded for every removed (folder, uid) membership so
        an ordinary synchronization does not restore a message the user
        deliberately deleted locally. When ``folder_id`` is given, only that
        folder's membership is removed; the message row, its EML and its
        attachments survive while another folder still references it, so a
        legitimate copy of the same email is never removed.

        Returns the EML/attachment relpaths whose payload files became
        unreferenced; the caller removes the files themselves.
        """
        if not message_ids:
            return {"messages": 0, "memberships": 0, "attachments": 0, "files": []}
        ids = [int(i) for i in message_ids if i is not None]
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            links = [dict(r) for r in self.conn.execute(
                f"SELECT message_id, folder_id, uid FROM message_folders "
                f"WHERE message_id IN ({placeholders})", ids)]
            msgs = {r["id"]: dict(r) for r in self.conn.execute(
                f"SELECT id, content_hash, eml_relpath FROM messages WHERE id IN ({placeholders})",
                ids)}
            removed_links = [lk for lk in links
                             if folder_id is None or lk["folder_id"] == int(folder_id)]
            for lk in removed_links:
                uv = self._folder_uidvalidity(lk["folder_id"])
                self.conn.execute(
                    "INSERT OR REPLACE INTO deleted_uids (folder_id, uid, uidvalidity, deleted_at) "
                    "VALUES (?,?,?,?)",
                    (lk["folder_id"], lk["uid"], uv, utc_now_iso()))
            if removed_links:
                self.conn.execute(
                    f"DELETE FROM message_folders WHERE message_id IN ({placeholders})"
                    + (" AND folder_id=?" if folder_id is not None else ""),
                    ids + ([int(folder_id)] if folder_id is not None else []))
            # A message row is only removed once no folder references it.
            survivor_rows = self.conn.execute(
                f"SELECT DISTINCT message_id FROM message_folders "
                f"WHERE message_id IN ({placeholders})", ids).fetchall()
            survivors = {r["message_id"] for r in survivor_rows}
            doomed = [i for i in ids if i in msgs and i not in survivors]
            files = []
            attachments = 0
            if doomed:
                doomp = ",".join("?" * len(doomed))
                for mid in doomed:
                    files.append(msgs[mid]["eml_relpath"])
                att_rows = self.conn.execute(
                    f"SELECT message_id, relpath FROM attachments "
                    f"WHERE message_id IN ({doomp})", doomed).fetchall()
                attachments = len(att_rows)
                files.extend(r["relpath"] for r in att_rows if r["relpath"])
                self.conn.execute(
                    f"DELETE FROM recipients WHERE message_id IN ({doomp})", doomed)
                self.conn.execute(
                    f"DELETE FROM attachments WHERE message_id IN ({doomp})", doomed)
                self.conn.execute(
                    f"DELETE FROM messages WHERE id IN ({doomp})", doomed)
            self.conn.commit()
        return {"messages": len(doomed), "memberships": len(removed_links),
                "attachments": attachments, "files": files}

    def referenced_attachment_paths(self, relpaths: list) -> set:
        """Subset of relpaths still referenced by at least one attachment row.

        Files referenced by a surviving message are never deleted."""
        if not relpaths:
            return set()
        with self._lock:
            marks = ",".join("?" * len(relpaths))
            return {r["relpath"] for r in self.conn.execute(
                f"SELECT DISTINCT relpath FROM attachments WHERE relpath IN ({marks})",
                list(relpaths))}

    # ------------------------------------------------------------- ingestion

    def message_hash_exists(self, content_hash: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM messages WHERE content_hash=?", (content_hash,)).fetchone()
            return row is not None

    def add_folder_link(self, message_id: int, folder_id: int, uid: int):
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO message_folders (message_id, folder_id, uid) VALUES (?,?,?)",
                (message_id, folder_id, uid))
            self.conn.commit()

    def folder_has_uid(self, folder_id: int, uid: int) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM message_folders WHERE folder_id=? AND uid=?", (folder_id, uid)
            ).fetchone()
            return row is not None

    def insert_message(self, msg: dict, folder_id: int, uid: int) -> bool:
        """Insert a parsed message. Returns True when a new row was created."""
        with self._lock:
            existing = self.conn.execute(
                "SELECT id FROM messages WHERE content_hash=?", (msg["content_hash"],)).fetchone()
            if existing:
                self.conn.execute(
                    "INSERT OR IGNORE INTO message_folders (message_id, folder_id, uid) "
                    "VALUES (?,?,?)", (existing["id"], folder_id, uid))
                self.conn.execute(
                    "UPDATE folders SET known_count = known_count + 1 WHERE id=?", (folder_id,))
                self.conn.commit()
                return False
            search_norm = msg.get("search_norm") or normalize_text(" \n ".join(
                str(msg.get(k) or "") for k in
                ("sender_name", "sender_email", "subject", "plain_body")))
            search_all = msg.get("search_all") or search.message_search_text(msg)
            cur = self.conn.execute(
                """INSERT INTO messages
                   (content_hash, message_id, date_utc, date_ts, sender_name, sender_email,
                    subject, plain_body, html_body, search_norm, search_all, size,
                    eml_relpath, created_at, complete)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (msg["content_hash"], msg["message_id"], msg["date_utc"], msg["date_ts"],
                 msg["sender_name"], msg["sender_email"],
                 msg["subject"], msg["plain_body"], msg["html_body"], search_norm,
                 search_all, msg["size"], msg["eml_relpath"], utc_now_iso(),
                 1 if msg.get("complete", True) else 0))
            message_id = cur.lastrowid
            self.conn.execute(
                "INSERT OR IGNORE INTO message_folders (message_id, folder_id, uid) VALUES (?,?,?)",
                (message_id, folder_id, uid))
            self.conn.execute(
                "UPDATE folders SET known_count = known_count + 1 WHERE id=?", (folder_id,))
            for rec in msg.get("recipients", []):
                self.conn.execute(
                    "INSERT INTO recipients (message_id, rtype, name, email) VALUES (?,?,?,?)",
                    (message_id, rec["type"], rec["name"], rec["email"]))
            for att in msg.get("attachments", []):
                self.conn.execute(
                    "INSERT INTO attachments (message_id, filename, filename_safe, size, "
                    "mime_type, relpath) VALUES (?,?,?,?,?,?)",
                    (message_id, att["filename"], att["filename_safe"], att["size"],
                     att["mime_type"], att["relpath"]))
            self._update_contacts(msg)
            self.conn.commit()
            return True

    def complete_message(self, partial_msg_id: int, msg: dict) -> bool:
        """Upgrade a partial (no-attachment) message row with the full data.

        Returns True when the row was upgraded, False when a complete row with
        the same content_hash already exists (the partial row is dropped to
        avoid duplicates). Existing full EML files are never overwritten.
        """
        with self._lock:
            existing = self.conn.execute(
                "SELECT id FROM messages WHERE content_hash=? AND complete=1",
                (msg["content_hash"],)).fetchone()
            if existing:
                self.conn.execute("DELETE FROM messages WHERE id=?", (partial_msg_id,))
                self.conn.commit()
                return False
            self.conn.execute(
                "UPDATE messages SET content_hash=?, eml_relpath=?, size=?, complete=1 "
                "WHERE id=?", (msg["content_hash"], msg["eml_relpath"],
                               msg["size"], partial_msg_id))
            self.conn.execute("DELETE FROM attachments WHERE message_id=?", (partial_msg_id,))
            for att in msg.get("attachments", []):
                self.conn.execute(
                    "INSERT INTO attachments (message_id, filename, filename_safe, size, "
                    "mime_type, relpath) VALUES (?,?,?,?,?,?)",
                    (partial_msg_id, att["filename"], att["filename_safe"], att["size"],
                     att["mime_type"], att["relpath"]))
            self.conn.commit()
            return True

    def partial_messages(self):
        """All messages archived without their attachment payloads."""
        with self._lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT id, content_hash FROM messages WHERE complete=0").fetchall()]

    def _update_contacts(self, msg: dict):
        now = msg["date_utc"] or utc_now_iso()
        pairs = []
        if msg.get("sender_email"):
            pairs.append((msg["sender_email"], msg.get("sender_name"), "received", now))
        for rec in msg.get("recipients", []):
            if rec.get("email"):
                pairs.append((rec["email"], rec.get("name"), "sent", now))
        domains = set()
        for email, name, kind, ts in pairs:
            norm = email.strip().lower()
            if "@" not in norm:
                continue
            domain = norm.split("@", 1)[1]
            domains.add((domain, ts))
            col = "received_count" if kind == "received" else "sent_count"
            row = self.conn.execute(
                "SELECT id, first_seen, last_seen FROM contacts WHERE email_norm=?", (norm,)
            ).fetchone()
            if row:
                first = row["first_seen"] if row["first_seen"] and row["first_seen"] < ts else ts
                last = ts if not row["last_seen"] or ts > row["last_seen"] else row["last_seen"]
                self.conn.execute(
                    f"UPDATE contacts SET {col}={col}+1, first_seen=?, last_seen=?, "
                    "display_name=COALESCE(NULLIF(display_name,''), ?) WHERE id=?",
                    (first, last, name or "", row["id"]))
            else:
                self.conn.execute(
                    "INSERT INTO contacts (email_norm, display_name, domain, first_seen, "
                    f"last_seen, {col}) VALUES (?,?,?,?,?,1)",
                    (norm, name or "", domain, ts, ts))
        for domain, ts in domains:
            row = self.conn.execute(
                "SELECT id, first_seen, last_seen FROM organizations WHERE domain=?", (domain,)
            ).fetchone()
            if row:
                first = row["first_seen"] if row["first_seen"] and row["first_seen"] < ts else ts
                last = ts if not row["last_seen"] or ts > row["last_seen"] else row["last_seen"]
                self.conn.execute(
                    "UPDATE organizations SET message_count=message_count+1, first_seen=?, "
                    "last_seen=? WHERE id=?", (first, last, row["id"]))
            else:
                self.conn.execute(
                    "INSERT INTO organizations (domain, first_seen, last_seen, message_count) "
                    "VALUES (?,?,?,1)", (domain, ts, ts))
        # keep organization contact_count in sync
        if domains:
            self.conn.execute(
                """UPDATE organizations SET contact_count=(
                       SELECT COUNT(*) FROM contacts c WHERE c.domain=organizations.domain)""")

    def advance_checkpoint(self, folder_id: int, uid: int):
        """Advance the folder's UID watermark over completed work.

        Called with the highest UID actually completed in a batch. UIDs below
        the watermark that failed are kept eligible for the next
        synchronization by the ``sync_failures`` table, and UIDs the user
        deleted locally are suppressed by ``deleted_uids``; the watermark
        itself is monotonic and only ever moves forward.
        """
        with self._lock:
            self.conn.execute(
                "UPDATE folders SET last_completed_uid=? WHERE id=? AND last_completed_uid<?",
                (uid, folder_id, uid))
            self.conn.commit()

    # -------------------------------------------------------------- querying

    def list_folders(self):
        with self._lock:
            rows = self.conn.execute(
                """SELECT f.id, f.imap_name, f.display_name, f.has_error,
                          (SELECT COUNT(*) FROM message_folders mf WHERE mf.folder_id=f.id) AS count
                   FROM folders f ORDER BY f.imap_name COLLATE NOCASE""").fetchall()
            return [dict(r) for r in rows]

    def _build_where(self, filters: dict, search_fragment=None):
        clauses, params = [], []
        if filters.get("folder_id"):
            clauses.append("m.id IN (SELECT message_id FROM message_folders WHERE folder_id=?)")
            params.append(int(filters["folder_id"]))
        if filters.get("date_from"):
            clauses.append("m.date_ts >= ?")
            params.append(filters["date_from"])
        if filters.get("date_to"):
            clauses.append("m.date_ts <= ?")
            params.append(filters["date_to"])
        if filters.get("party"):
            clauses.append(
                "(m.sender_email LIKE ? OR m.sender_name LIKE ? OR EXISTS("
                "SELECT 1 FROM recipients r WHERE r.message_id=m.id AND "
                "(r.email LIKE ? OR r.name LIKE ?)))")
            like = f"%{filters['party'].replace('%', '\\%')}%"
            params.extend([like, like, like, like])
        if search_fragment is not None:
            clauses.append(search_fragment["sql"])
            params.extend(search_fragment["params"])
        elif filters.get("search"):
            norm = normalize_text(filters["search"])
            like = f"%{norm.replace('%', '\\%')}%"
            clauses.append("m.search_norm LIKE ? ESCAPE '\\'")
            params.append(like)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def _search_fragment(self, raw_query: str, suggest: bool = True):
        """Resolve a user search query through the layered search plan."""
        if not raw_query or not raw_query.strip():
            return None, None
        backend = search.SearchBackend(self.conn, self._fts_ok)
        result = search.plan(backend, raw_query, suggest=suggest)
        return result["fragment"], result["suggestion"]

    def count_messages(self, filters: dict) -> int:
        where, params = self._build_where(filters)
        with self._lock:
            row = self.conn.execute(
                f"SELECT COUNT(*) AS n FROM messages m {where}", params).fetchone()
            return row["n"] if row else 0

    def search_query(self, filters: dict, sort: str = "newest", page: int = 1,
                     page_size: int = 50):
        """Layered message search: count + page + suggestion in one round."""
        suggestion = None
        fragment = None
        raw = filters.get("search") if isinstance(filters, dict) else None
        if raw and str(raw).strip():
            fragment, suggestion = self._search_fragment(str(raw))
        where, params = self._build_where(filters, fragment)
        sort_map = {
            "newest": "m.date_ts DESC NULLS LAST, m.id DESC",
            "oldest": "m.date_ts ASC NULLS LAST, m.id ASC",
            "sender": "m.sender_name ASC NULLS LAST, m.date_ts DESC",
            "subject": "m.subject ASC NULLS LAST, m.date_ts DESC",
        }
        order = sort_map.get(sort, sort_map["newest"])
        offset = max(0, (page - 1) * page_size)
        with self._lock:
            row = self.conn.execute(
                f"SELECT COUNT(*) AS n FROM messages m {where}", params).fetchone()
            total = row["n"] if row else 0
            sql = f"""SELECT m.id, m.message_id, m.date_utc, m.date_ts, m.sender_name,
                             m.sender_email, m.subject, m.plain_body, m.size,
                             (SELECT COUNT(*) FROM attachments a WHERE a.message_id=m.id) AS att_count,
                             (SELECT GROUP_CONCAT(f.display_name, ', ') FROM message_folders mf
                              JOIN folders f ON f.id=mf.folder_id WHERE mf.message_id=m.id) AS folders
                      FROM messages m {where} ORDER BY {order} LIMIT ? OFFSET ?"""
            rows = self.conn.execute(sql, params + [page_size, offset]).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                if d.get("plain_body"):
                    d["preview"] = d["plain_body"][:160]
                results.append(d)
        return {"total": total, "page": int(page or 1), "rows": results,
                "suggestion": suggestion}

    def query_messages(self, filters: dict, sort: str = "newest", page: int = 1,
                       page_size: int = 50):
        where, params = self._build_where(filters)
        sort_map = {
            "newest": "m.date_ts DESC NULLS LAST, m.id DESC",
            "oldest": "m.date_ts ASC NULLS LAST, m.id ASC",
            "sender": "m.sender_name ASC NULLS LAST, m.date_ts DESC",
            "subject": "m.subject ASC NULLS LAST, m.date_ts DESC",
        }
        order = sort_map.get(sort, sort_map["newest"])
        offset = max(0, (page - 1) * page_size)
        sql = f"""SELECT m.id, m.message_id, m.date_utc, m.date_ts, m.sender_name,
                         m.sender_email, m.subject, m.plain_body, m.size,
                         (SELECT COUNT(*) FROM attachments a WHERE a.message_id=m.id) AS att_count,
                         (SELECT GROUP_CONCAT(f.display_name, ', ') FROM message_folders mf
                          JOIN folders f ON f.id=mf.folder_id WHERE mf.message_id=m.id) AS folders
                  FROM messages m {where} ORDER BY {order} LIMIT ? OFFSET ?"""
        with self._lock:
            rows = self.conn.execute(sql, params + [page_size, offset]).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                if d.get("plain_body"):
                    d["preview"] = d["plain_body"][:160]
                results.append(d)
            return results

    def get_message(self, message_id: int):
        with self._lock:
            row = self.conn.execute(
                """SELECT m.*, (SELECT GROUP_CONCAT(f.display_name, ' | ') FROM message_folders mf
                   JOIN folders f ON f.id=mf.folder_id WHERE mf.message_id=m.id) AS folder_names
                   FROM messages m WHERE m.id=?""", (message_id,)).fetchone()
            if not row:
                return None
            msg = dict(row)
            msg["recipients"] = [dict(r) for r in self.conn.execute(
                "SELECT rtype, name, email FROM recipients WHERE message_id=? ORDER BY rtype",
                (message_id,)).fetchall()]
            msg["attachments"] = [dict(a) for a in self.conn.execute(
                "SELECT id, filename, filename_safe, size, mime_type, relpath "
                "FROM attachments WHERE message_id=?", (message_id,)).fetchall()]
            msg["complete"] = bool(msg.get("complete", 1))
            return msg

    def message_attachment(self, message_id: int, attachment_id: int):
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM attachments WHERE id=? AND message_id=?",
                (attachment_id, message_id)).fetchone()

    def archive_stats(self):
        with self._lock:
            stats = {}
            row = self.conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS size FROM messages").fetchone()
            stats["messages"] = row["n"]
            stats["total_size"] = row["size"]
            stats["contacts"] = self.conn.execute(
                "SELECT COUNT(*) AS n FROM contacts").fetchone()["n"]
            stats["attachments"] = self.conn.execute(
                "SELECT COUNT(*) AS n FROM attachments").fetchone()["n"]
            stats["organizations"] = self.conn.execute(
                "SELECT COUNT(*) AS n FROM organizations").fetchone()["n"]
            stats["folders"] = self.conn.execute(
                "SELECT COUNT(*) AS n FROM folders").fetchone()["n"]
            return stats

    def start_sync_log(self) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO sync_log (started_at, status) VALUES (?, 'running')",
                (utc_now_iso(),))
            self.conn.commit()
            return cur.lastrowid

    def finish_sync_log(self, log_id: int, status: str, new_messages: int,
                        deduped: int, errors: int, elapsed: float):
        with self._lock:
            self.conn.execute(
                "UPDATE sync_log SET finished_at=?, status=?, new_messages=?, deduped=?, "
                "errors=?, elapsed_seconds=? WHERE id=?",
                (utc_now_iso(), status, new_messages, deduped, errors, elapsed, log_id))
            self.conn.commit()

    # ------------------------------------------------------------- report api
    #
    # Scope-driven reporting: every worksheet of a report is derived from
    # exactly the same message set (same account, date range, folders,
    # direction, drafts handling and keyword). Contact and domain statistics
    # are computed from the filtered messages themselves and can therefore
    # never be shown beside message counts from a wider scope.

    def _report_where(self, scope: dict):
        """WHERE clause selecting the messages of one report scope.

        `folder_ids` (empty = every folder), `date_from`/`date_to` (epoch
        seconds, inclusive) and `party`/`search` mirror the archive view so a
        report always matches what the user searched for.
        """
        clauses, params = [], []
        folder_ids = [int(f) for f in (scope.get("folder_ids") or []) if f]
        if folder_ids:
            marks = ",".join("?" * len(folder_ids))
            clauses.append(
                "m.id IN (SELECT message_id FROM message_folders "
                f"WHERE folder_id IN ({marks}))")
            params.extend(folder_ids)
        if scope.get("date_from"):
            clauses.append("m.date_ts >= ?")
            params.append(int(scope["date_from"]))
        if scope.get("date_to"):
            clauses.append("m.date_ts <= ?")
            params.append(int(scope["date_to"]))
        if scope.get("party"):
            clauses.append(
                "(m.sender_email LIKE ? OR m.sender_name LIKE ? OR EXISTS("
                "SELECT 1 FROM recipients r WHERE r.message_id=m.id AND "
                "(r.email LIKE ? OR r.name LIKE ?)))")
            like = f"%{str(scope['party']).replace('%', '\\%')}%"
            params.extend([like, like, like, like])
        raw = scope.get("search")
        if raw and str(raw).strip():
            fragment, _ = self._search_fragment(str(raw), suggest=False)
            if fragment:
                clauses.append(fragment["sql"])
                params.extend(fragment["params"])
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def report_folders(self):
        """Folders present in the archive, offered as report scope choices."""
        with self._lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT id, imap_name, display_name FROM folders "
                "ORDER BY imap_name COLLATE NOCASE").fetchall()]

    def report_message_count(self, scope: dict) -> int:
        where, params = self._report_where(scope)
        with self._lock:
            row = self.conn.execute(
                f"SELECT COUNT(*) FROM messages m {where}", params).fetchone()
            return row[0] if row else 0

    def report_messages(self, scope: dict, with_body: bool = False,
                        limit: int = 100000):
        """Messages of the scope with their folder membership.

        Folder membership is needed to tell drafts from real correspondence
        and to describe each message without exporting internal paths.
        `with_body` defaults to False so a report without bodies never
        transfers large text payloads at all.
        """
        where, params = self._report_where(scope)
        body = ", m.plain_body" if with_body else ""
        sql = f"""SELECT m.id, m.message_id, m.date_utc, m.date_ts, m.sender_name,
                         m.sender_email, m.subject, m.size, m.eml_relpath{body},
                         GROUP_CONCAT(DISTINCT f.imap_name) AS folder_imap,
                         GROUP_CONCAT(DISTINCT f.display_name) AS folder_display
                  FROM messages m
                  LEFT JOIN message_folders mf ON mf.message_id = m.id
                  LEFT JOIN folders f ON f.id = mf.folder_id
                  {where}
                  GROUP BY m.id
                  ORDER BY m.date_ts DESC NULLS LAST, m.id DESC LIMIT ?"""
        with self._lock:
            return [dict(r) for r in
                    self.conn.execute(sql, params + [limit]).fetchall()]

    def _scope_participants_sql(self, message_ids, own_addresses=(),
                                sent_ids=()):
        """Union of the participants of the selected messages.

        The account's own address and aliases are excluded, while other
        addresses on the same domain stay included. The sender side ('from')
        is who a message came from; the recipient side ('to') is limited to
        messages the account actually sent (`sent_ids`), so contact and
        domain aggregation share one definition with the report summary
        (drafts are never sent messages).

        Empty dates become NULL: MIN/MAX then ignore undated messages
        instead of letting the empty string sort first and erase the known
        first/last dates.

        Returns (union_sql, own_clause, params) for the calling queries.
        """
        ids = ",".join("?" * len(message_ids))
        own = [str(a).strip().lower() for a in (own_addresses or []) if a]
        sent = [i for i in (sent_ids or []) if i is not None]
        # Without sent messages the recipient side contributes nothing: an
        # empty IN () list is not valid SQL, so the side is disabled outright.
        sent_clause = (f"AND r.message_id IN ({','.join('?' * len(sent))})"
                       if sent else "AND 0")
        own_clause = (f"AND lower(u.addr) NOT IN ({','.join('?' * len(own))})"
                      if own else "")
        union = f"""(
                    SELECT m.id AS mid, m.sender_email AS addr, m.sender_name AS nm,
                           NULLIF(m.date_utc, '') AS ts, 'from' AS side
                    FROM messages m WHERE m.id IN ({ids})
                    UNION ALL
                    SELECT r.message_id, r.email, r.name,
                           NULLIF(m.date_utc, ''), 'to' AS side
                    FROM recipients r
                    JOIN messages m ON m.id = r.message_id
                    WHERE r.message_id IN ({ids}) {sent_clause}
                  )"""
        params = list(message_ids) * 2 + sent + own
        return union, own_clause, params

    def report_contacts(self, message_ids, own_addresses=(), sent_ids=()):
        """External correspondents derived only from the selected messages.

        The account's own address and registered aliases are excluded, so
        rankings and the unique-correspondent metric cover actual
        counterparties only. Per-contact incoming/outgoing uses the same
        definitions as the report summary: incoming counts messages
        received from the correspondent (their sender-side messages),
        outgoing counts messages the account actually sent to them
        (`sent_ids`; drafts never qualify). First/last seen ignore undated
        messages but are never blanked by them.

        Every returned row has activity by construction; zero-activity rows
        exist only in the explicitly requested complete directory
        (report_contact_directory).
        """
        if not message_ids:
            return []
        union, own_clause, params = self._scope_participants_sql(
            message_ids, own_addresses, sent_ids)
        sql = f"""SELECT lower(u.addr) AS email,
                         GROUP_CONCAT(DISTINCT u.nm) AS names,
                         substr(lower(u.addr), instr(lower(u.addr), '@') + 1) AS domain,
                         COUNT(DISTINCT u.mid) AS messages,
                         SUM(CASE WHEN u.side='from' THEN 1 ELSE 0 END) AS incoming,
                         SUM(CASE WHEN u.side='to' THEN 1 ELSE 0 END) AS outgoing,
                         MIN(u.ts) AS first_seen, MAX(u.ts) AS last_seen
                  FROM {union} AS u
                  WHERE u.addr IS NOT NULL AND u.addr <> ''
                        AND instr(u.addr, '@') > 0 {own_clause}
                  GROUP BY lower(u.addr)
                  ORDER BY messages DESC, email ASC"""
        with self._lock:
            return [dict(r) for r in
                    self.conn.execute(sql, params).fetchall()]

    def report_domains(self, message_ids, own_addresses=(), sent_ids=()):
        """External email domains derived only from the selected messages.

        Uses exactly the same participant set as report_contacts: the
        account's own addresses are excluded (so the account's own domain
        only counts genuine external contacts on it), and every count
        (messages, unique participants) follows the counterparty definition
        of the report summary.
        """
        if not message_ids:
            return []
        union, own_clause, params = self._scope_participants_sql(
            message_ids, own_addresses, sent_ids)
        sql = f"""SELECT lower(substr(u.addr, instr(u.addr, '@') + 1)) AS domain,
                         COUNT(DISTINCT lower(u.addr)) AS contacts,
                         COUNT(DISTINCT u.mid) AS messages,
                         MIN(u.ts) AS first_seen, MAX(u.ts) AS last_seen
                  FROM {union} AS u
                  WHERE u.addr IS NOT NULL AND instr(u.addr, '@') > 0 {own_clause}
                  GROUP BY lower(substr(u.addr, instr(u.addr, '@') + 1))
                  ORDER BY messages DESC, domain ASC"""
        with self._lock:
            return [dict(r) for r in
                    self.conn.execute(sql, params).fetchall()]

    def report_attachments(self, message_ids):
        """Attachment metadata of the selected messages with their context."""
        if not message_ids:
            return []
        ids = ",".join("?" * len(message_ids))
        sql = f"""SELECT a.message_id, a.filename, a.filename_safe, a.mime_type,
                         a.size AS bytes, m.date_utc, m.subject, m.sender_email,
                         m.sender_name
                  FROM attachments a JOIN messages m ON m.id = a.message_id
                  WHERE a.message_id IN ({ids})
                  ORDER BY m.date_ts DESC NULLS LAST, a.id ASC"""
        with self._lock:
            return [dict(r) for r in
                    self.conn.execute(sql, list(message_ids)).fetchall()]

    def report_contact_directory(self):
        """Complete contact directory of the account.

        Includes contacts with no message inside the report scope; used only
        when the user explicitly asks for the complete directory.
        """
        with self._lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT email_norm, display_name, domain, first_seen, last_seen "
                "FROM contacts ORDER BY email_norm").fetchall()]

    # Recipient groups of the selected messages (to/cc/bcc columns).

    def report_recipients_for(self, message_ids):
        """to/cc/bcc rows of the selected messages, grouped by message."""
        if not message_ids:
            return []
        ids = ",".join("?" * len(message_ids))
        with self._lock:
            return [dict(r) for r in self.conn.execute(
                f"SELECT message_id, rtype, name, email FROM recipients "
                f"WHERE message_id IN ({ids}) "
                f"ORDER BY message_id, rtype, email", list(message_ids)).fetchall()]


def content_hash_of(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def eml_relpath_for(content_hash: str) -> str:
    return os.path.join("eml", content_hash[:2], content_hash + ".eml")
