"""MailDaan account registry and per-account storage paths.

Each account owns an independent folder containing its SQLite archive,
EML files and attachments. Passwords are never stored; they are kept in
memory only while a synchronization is running.

A MailDaan 1.0 single-account archive (data/maildaan.db) is adopted
non-destructively as the first account, referenced in place.
"""

import datetime as _dt
import json
import os
import uuid

import config


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _new_id() -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, "maildaan://" + uuid.uuid4().hex).hex[:16]


def load_registry() -> list:
    raw = None
    path = config.get_registry_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            raw = None
    if not isinstance(raw, list):
        return []
    return [a for a in raw if isinstance(a, dict) and a.get("id")]


def save_registry(accounts: list) -> None:
    path = config.get_registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(accounts, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def all_accounts() -> list:
    return load_registry()


def get_account(account_id: str):
    if not account_id:
        return None
    for account in load_registry():
        if account["id"] == account_id:
            return account
    return None


def public_account(account) -> dict:
    """Registry record as seen by the frontend (no secrets involved)."""
    return {
        "id": account["id"],
        "display_name": account.get("display_name") or "",
        "email": account.get("email") or "",
        "imap_host": account.get("imap_host") or "",
        "imap_port": account.get("imap_port") or 993,
        "last_sync_at": account.get("last_sync_at") or "",
        "last_sync_status": account.get("last_sync_status") or "",
        "storage": account.get("storage") or "standard",
    }


def account_paths(account) -> dict:
    """Resolve the independent storage locations for one account."""
    if account.get("storage") == "legacy":
        # MailDaan 1.0 archive: referenced in place, never moved or copied.
        root = config.get_legacy_data_dir()
        db = os.path.join(root, "maildaan.db")
    else:
        root = config.get_account_dir(account["id"])
        db = os.path.join(root, "archive.db")
    return {
        "root": root,
        "db": db,
        "eml": os.path.join(root, "eml"),
        "attachments": os.path.join(root, "attachments"),
    }


def ensure_dirs(account) -> None:
    if account.get("storage") == "legacy":
        return
    paths = account_paths(account)
    for path in (paths["eml"], paths["attachments"]):
        os.makedirs(path, exist_ok=True)


def create_account(display_name: str, email: str, host: str, port: int) -> dict:
    """Register a new account with its own empty archive storage."""
    account = {
        "id": _new_id(),
        "display_name": (display_name or "").strip(),
        "email": (email or "").strip().lower(),
        "imap_host": (host or "").strip(),
        "imap_port": int(port or 993),
        "storage": "standard",
        "created_at": utc_now_iso(),
        "last_sync_at": "",
        "last_sync_status": "",
    }
    ensure_dirs(account)
    accounts = load_registry()
    accounts.append(account)
    save_registry(accounts)
    return account


def update_account(account_id: str, **fields) -> object:
    accounts = load_registry()
    for account in accounts:
        if account["id"] == account_id:
            for key in ("display_name", "email", "imap_host", "imap_port",
                        "last_sync_at", "last_sync_status"):
                if key in fields and fields[key] is not None:
                    if key == "imap_port":
                        try:
                            account[key] = int(fields[key])
                        except (TypeError, ValueError):
                            continue
                    elif key in ("email",):
                        account[key] = str(fields[key]).strip().lower()
                    elif key in ("display_name", "imap_host"):
                        account[key] = str(fields[key]).strip()
                    else:
                        account[key] = fields[key]
            save_registry(accounts)
            return account
    return None


def remove_account(account_id: str) -> bool:
    """Remove an account from the registry. Local archive data is kept."""
    accounts = load_registry()
    remaining = [a for a in accounts if a["id"] != account_id]
    if len(remaining) == len(accounts):
        return False
    save_registry(remaining)
    return True


def adopt_legacy_account() -> object:
    """Register the MailDaan 1.0 single-account archive, if present.

    Nothing is moved, copied or deleted: the account record simply points
    at the existing data directory, so all previously archived emails stay
    exactly where they were.
    """
    if all_accounts():
        return None
    if not os.path.exists(config.get_legacy_db_path()):
        return None
    legacy = None
    path = os.path.join(config.get_legacy_data_dir(), "settings.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                legacy = json.load(fh)
        except (OSError, ValueError):
            legacy = None
    legacy = legacy if isinstance(legacy, dict) else {}
    account = {
        "id": _new_id(),
        "display_name": "",
        "email": (legacy.get("username") or "").strip().lower(),
        "imap_host": (legacy.get("imap_host") or "").strip(),
        "imap_port": int(legacy.get("imap_port") or 993),
        "storage": "legacy",
        "created_at": utc_now_iso(),
        "last_sync_at": "",
        "last_sync_status": "",
    }
    save_registry([account])
    return account
