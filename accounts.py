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
import shutil
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


def _removed_archives_path() -> str:
    """Index of archives kept on disk after a 'remove account only' action."""
    return os.path.join(config.get_base_dir(), "removed_archives.json")


def load_removed_archives() -> list:
    path = _removed_archives_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return []
    return [a for a in raw if isinstance(a, dict) and a.get("id")]


def _save_removed_archives(records: list) -> None:
    path = _removed_archives_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def record_removed_archive(account) -> None:
    """Remember an archive deliberately kept on disk (remove-account-only).

    The record carries the original account identity (id, email, host) so the
    archive can be reattached exactly when the same mailbox is added again —
    its checkpoints, tombstones and downloaded messages stay usable and the
    archive is never silently orphaned."""
    try:
        records = load_removed_archives()
        records = [r for r in records if r.get("id") != account.get("id")]
        records.append({
            "id": account.get("id"),
            "display_name": account.get("display_name") or "",
            "email": (account.get("email") or "").strip().lower(),
            "imap_host": account.get("imap_host") or "",
            "imap_port": account.get("imap_port") or 993,
            "storage": account.get("storage") or "standard",
            "removed_at": utc_now_iso(),
        })
        _save_removed_archives(records)
    except OSError:
        pass  # the archive stays on disk even if the index cannot be kept


def pop_removed_archive(email: str):
    """Take back the retained archive of a mailbox, if one was kept.

    Reattaching reuses the original account id, so the existing directory,
    its SQLite archive, EML files and attachments are used in place."""
    if not email:
        return None
    wanted = str(email).strip().lower()
    records = load_removed_archives()
    match = next((r for r in records
                  if (r.get("email") or "").strip().lower() == wanted
                  and r.get("storage") == "standard"), None)
    if match is None:
        return None
    if not os.path.isdir(config.get_account_dir(match["id"])):
        # Nothing was actually retained (for example a separate data Removal
        # already deleted it): drop the stale index entry.
        _save_removed_archives([r for r in records if r.get("id") != match["id"]])
        return None
    _save_removed_archives([r for r in records if r.get("id") != match["id"]])
    return match


def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def remove_account_data(account) -> dict:
    """Delete exactly one account's dedicated local archive directory.

    The deletion target is validated against the expected account directory
    before anything is removed: path traversal and deletion of shared
    application directories are impossible by construction. Server-side
    data is never touched — this only clears the local copy.

    Returns {'removed': True, 'bytes': n}. Raises OSError/ValueError with a
    concrete message when the directory cannot be deleted; the caller must
    then keep the account registered instead of reporting success."""
    account_id = account.get("id")
    if not account_id:
        raise ValueError("unknown account")
    storage_kind = account.get("storage")
    targets = []
    if storage_kind == "legacy":
        # The MailDaan 1.0 folder also holds the legacy settings file, so
        # only its archive contents are removed, not the folder itself.
        root = config.get_legacy_data_dir()
        targets = [os.path.join(root, "maildaan.db"),
                   os.path.join(root, "eml"),
                   os.path.join(root, "attachments")]
        description = "legacy archive"
    else:
        accounts_root = os.path.normcase(os.path.realpath(config.get_accounts_root()))
        expected = config.get_account_dir(account_id)
        real_nc = os.path.normcase(os.path.realpath(expected))
        # Constrain deletion to exactly <accounts_root>\<account_id>: neither
        # path traversal nor deletion of a shared directory is possible.
        if os.path.basename(real_nc) != account_id \
                or os.path.dirname(real_nc) != accounts_root:
            raise ValueError("the account directory does not match its expected path")
        if not os.path.isdir(accounts_root):
            raise OSError("the accounts directory is not accessible")
        targets = [expected]
        description = "account directory"
    existing = [t for t in targets if os.path.exists(t)]
    if not existing:
        return {"removed": True, "bytes": 0, "not_found": True}
    total = sum(_dir_size(t) for t in existing)
    failures = []
    for target in existing:
        try:
            if os.path.isdir(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
        except OSError as exc:
            failures.append(f"{os.path.basename(target)}: {exc}")
    if failures:
        raise OSError("could not delete the " + description +
                      " completely: " + "; ".join(failures))
    # A retained-archive index entry is obsolete once its data is gone.
    try:
        records = [r for r in load_removed_archives()
                   if r.get("id") != account_id]
        _save_removed_archives(records)
    except OSError:
        pass
    return {"removed": True, "bytes": total}


def create_account(display_name: str, email: str, host: str, port: int) -> dict:
    """Register a new account with its own empty archive storage.

    A previously retained archive of the same mailbox is reattached in
    place: the original account id and directory are reused, so the user
    gets back their checkpoints, EML files and attachments."""
    removed = pop_removed_archive(email)
    if removed:
        account = {
            "id": removed["id"],
            "display_name": (display_name or "").strip() or removed.get("display_name", ""),
            "email": (email or "").strip().lower(),
            "imap_host": (host or "").strip(),
            "imap_port": int(port or 993),
            "storage": "standard",
            "created_at": removed.get("removed_at") or utc_now_iso(),
            "last_sync_at": "",
            "last_sync_status": "",
        }
        ensure_dirs(account)
        accounts = load_registry()
        if not any(a["id"] == account["id"] for a in accounts):
            accounts.append(account)
            save_registry(accounts)
        return account
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
