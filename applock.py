"""Optional application-password lock for MailDaan.

When the user enables it, an Argon2id hash of the password (never the
plaintext password) is stored in the user data directory and MailDaan asks
for the password after every start before anything else becomes usable.

This is an application-level *access lock*: it controls entry into the
MailDaan interface. It is not encryption of the underlying email archive,
it is not user account management, and there is deliberately no password
reset bypass.
"""

import json
import os
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

import config


def get_lock_file() -> str:
    return os.path.join(config.get_base_dir(), "app_password.json")


def _hasher() -> PasswordHasher:
    # Argon2id with a unique random salt, high memory cost and argon2-cffi
    # defaults: the encoded hash stores the salt and parameters with it.
    return PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4,
                          hash_len=32, salt_len=16)


class AppLock:
    """Application password state shared by the bridge and startup window.

    Verification state lives in memory only; the persisted record is the
    encoded Argon2id hash plus the creation timestamp.
    """

    def __init__(self):
        self.enabled = False
        self._hash = None
        self._created_at = None
        self._failures = 0
        self._load()

    def _load(self) -> None:
        path = get_lock_file()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                record = json.load(fh)
            if isinstance(record, dict) and isinstance(record.get("hash"), str):
                self._hash = record["hash"]
                self._created_at = record.get("created_at")
                self.enabled = True
        except (OSError, ValueError):
            self.enabled = False
            self._hash = None

    def enable(self, password: str) -> None:
        """Store a new application password (hash only)."""
        record = {
            "hash": _hasher().hash(password),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "scheme": "argon2id",
        }
        path = get_lock_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        self._hash = record["hash"]
        self._created_at = record["created_at"]
        self.enabled = True
        self._failures = 0

    def disable(self) -> None:
        """Remove the stored application password."""
        path = get_lock_file()
        try:
            os.remove(path)
        except OSError:
            pass
        self.enabled = False
        self._hash = None
        self._failures = 0

    def verify(self, password) -> bool:
        """Check a password against the stored Argon2id hash.

        A progressive backoff is applied after repeated incorrect attempts.
        Password values are never logged.
        """
        if not self.enabled:
            return True
        if not password:
            self._failures += 1
            return False
        if self._failures >= 3:
            time.sleep(min(0.5 * (self._failures - 2), 8.0))
        try:
            ok = _hasher().verify(self._hash, password)
        except (VerifyMismatchError, InvalidHashError):
            ok = False
        except Exception:
            ok = False
        if ok:
            self._failures = 0
        else:
            self._failures += 1
        return bool(ok)
