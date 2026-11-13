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
    created_at TEXT
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
                    eml_relpath, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (msg["content_hash"], msg["message_id"], msg["date_utc"], msg["date_ts"],
                 msg["sender_name"], msg["sender_email"],
                 msg["subject"], msg["plain_body"], msg["html_body"], search_norm,
                 search_all, msg["size"], msg["eml_relpath"], utc_now_iso()))
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
        """Only called after a batch fully succeeds — never past failed work."""
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

    # ------------------------------------------------------------- export api

    def export_messages(self, filters: dict, sort: str = "newest", limit: int = 20000):
        fragment = None
        raw = filters.get("search") if isinstance(filters, dict) else None
        if raw and str(raw).strip():
            # Same direct search layers as the archive view so the exported
            # scope matches what the user searched for (no fuzzy guessing).
            fragment, _ = self._search_fragment(str(raw), suggest=False)
        where, params = self._build_where(filters, fragment)
        sort_map = {"newest": "m.date_ts DESC", "oldest": "m.date_ts ASC",
                    "sender": "m.sender_name ASC", "subject": "m.subject ASC"}
        order = sort_map.get(sort, sort_map["newest"])
        sql = f"""SELECT m.id, m.date_utc, m.sender_name, m.sender_email, m.subject,
                         m.plain_body, m.eml_relpath, m.size,
                         (SELECT GROUP_CONCAT(f.display_name, ' | ') FROM message_folders mf
                          JOIN folders f ON f.id=mf.folder_id WHERE mf.message_id=m.id) AS folders
                  FROM messages m {where} ORDER BY {order} LIMIT ?"""
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params + [limit]).fetchall()]

    def export_recipients_for(self, message_ids):
        if not message_ids:
            return []
        ids = ",".join("?" * len(message_ids))
        with self._lock:
            return [dict(r) for r in self.conn.execute(
                f"SELECT message_id, rtype, name, email FROM recipients "
                f"WHERE message_id IN ({ids})", message_ids).fetchall()]

    def export_attachments_for(self, message_ids):
        if not message_ids:
            return []
        ids = ",".join("?" * len(message_ids))
        with self._lock:
            return [dict(r) for r in self.conn.execute(
                f"SELECT message_id, filename, size FROM attachments "
                f"WHERE message_id IN ({ids})", message_ids).fetchall()]

    def export_contacts(self, message_ids=None):
        if message_ids is None:
            with self._lock:
                return [dict(r) for r in self.conn.execute(
                    "SELECT email_norm, display_name, domain, first_seen, last_seen, "
                    "sent_count, received_count FROM contacts ORDER BY email_norm").fetchall()]
        # derive contacts only from the selected messages
        ids = ",".join("?" * len(message_ids)) if message_ids else "NULL"
        sql = f"""SELECT c.email_norm, c.display_name, c.domain, c.first_seen, c.last_seen,
                         SUM(CASE WHEN m.id IS NOT NULL THEN 1 ELSE 0 END) AS msg_count,
                         SUM(CASE WHEN m.sender_email = c.email_norm THEN 1 ELSE 0 END) AS received_count,
                         SUM(CASE WHEN m.sender_email <> c.email_norm THEN 1 ELSE 0 END) AS sent_count
                  FROM contacts c
                  LEFT JOIN messages m ON m.id IN ({ids}) AND (
                      m.sender_email = c.email_norm OR EXISTS (
                          SELECT 1 FROM recipients r WHERE r.message_id=m.id AND r.email=c.email_norm))
                  GROUP BY c.id ORDER BY c.email_norm"""
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, message_ids or []).fetchall()]

    def export_organizations(self, message_ids=None):
        if message_ids is None:
            with self._lock:
                return [dict(r) for r in self.conn.execute(
                    "SELECT domain, contact_count, message_count, first_seen, last_seen "
                    "FROM organizations ORDER BY domain").fetchall()]
        ids = ",".join("?" * len(message_ids)) if message_ids else "NULL"
        sql = f"""SELECT o.domain, o.contact_count,
                         COUNT(DISTINCT m.id) AS message_count, MIN(m.date_utc) AS first_seen,
                         MAX(m.date_utc) AS last_seen
                  FROM organizations o
                  LEFT JOIN messages m ON m.id IN ({ids}) AND (
                      substr(m.sender_email, instr(m.sender_email,'@')+1) = o.domain OR EXISTS (
                          SELECT 1 FROM recipients r JOIN messages rm ON rm.id=r.message_id
                          WHERE rm.id=m.id AND substr(r.email, instr(r.email,'@')+1)=o.domain))
                  GROUP BY o.id ORDER BY o.domain"""
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, message_ids or []).fetchall()]


def content_hash_of(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def eml_relpath_for(content_hash: str) -> str:
    return os.path.join("eml", content_hash[:2], content_hash + ".eml")
