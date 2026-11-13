"""MailDaan configuration and local path management.

All user data lives under %LOCALAPPDATA%\\MailDaan and is never stored
inside the application source or executable directory.
"""

import json
import os
import sys

APP_NAME = "MailDaan"
APP_VERSION = "1.0.0"
DEVELOPER = "Titanium"
DEVELOPER_URL = "https://github.com/TahaSarhangi"
COPYRIGHT = "© 2026 MailDaan"


def get_base_dir() -> str:
    """Return the per-user MailDaan base directory (never the install dir)."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        local = os.path.expanduser("~\\AppData\\Local")
    base = os.path.join(local, "MailDaan")
    return base


def get_data_dir() -> str:
    """Return the user data directory, creating it when needed."""
    path = os.path.join(get_base_dir(), "data")
    os.makedirs(path, exist_ok=True)
    return path


def get_db_path() -> str:
    return os.path.join(get_data_dir(), "maildaan.db")


def get_eml_dir() -> str:
    path = os.path.join(get_data_dir(), "eml")
    os.makedirs(path, exist_ok=True)
    return path


def get_attachments_dir() -> str:
    path = os.path.join(get_data_dir(), "attachments")
    os.makedirs(path, exist_ok=True)
    return path


def get_exports_dir() -> str:
    path = os.path.join(get_data_dir(), "exports")
    os.makedirs(path, exist_ok=True)
    return path


def get_log_dir() -> str:
    """Return the directory for runtime diagnostic logs (no user content)."""
    path = os.path.join(get_base_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def get_settings_path() -> str:
    return os.path.join(get_data_dir(), "settings.json")


DEFAULT_SETTINGS = {
    "language": None,          # 'fa' | 'en' | None (first run)
    "imap_host": "",
    "imap_port": 993,
    "username": "",
    "configured": False,       # set True once IMAP settings accepted
    "first_run_complete": False,
}


def load_settings() -> dict:
    path = get_settings_path()
    settings = dict(DEFAULT_SETTINGS)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            if isinstance(saved, dict):
                settings.update(saved)
        except (OSError, ValueError):
            pass
    return settings


def save_settings(settings: dict) -> None:
    path = get_settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _packaged_base() -> str:
    """Base directory of bundled resources when running as a PyInstaller app."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_ui_dir() -> str:
    """Return the directory containing bundled frontend UI assets."""
    return os.path.join(_packaged_base(), "ui")


def get_asset_dir() -> str:
    return os.path.join(_packaged_base(), "assets")

