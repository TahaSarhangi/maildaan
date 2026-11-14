"""MailDaan configuration and local path management.

All user data lives under %LOCALAPPDATA%\\MailDaan and is never stored
inside the application source or executable directory.
"""

import json
import os
import sys

APP_NAME = "MailDaan"
APP_VERSION = "1.1.3"
DEVELOPER = "Titanium"
DEVELOPER_URL = "https://github.com/TahaSarhangi"
COPYRIGHT = "© 2026 MailDaan"


def get_base_dir() -> str:
    """Return the per-user MailDaan base directory (never the install dir)."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        local = os.path.expanduser("~\\AppData\\Local")
    return os.path.join(local, "MailDaan")


def get_legacy_data_dir() -> str:
    """Directory used by MailDaan 1.0 single-account archives (kept intact)."""
    return os.path.join(get_base_dir(), "data")


def get_legacy_db_path() -> str:
    return os.path.join(get_legacy_data_dir(), "maildaan.db")


def get_accounts_root() -> str:
    """Root directory holding one folder per registered email account."""
    return os.path.join(get_base_dir(), "accounts")


def get_account_dir(account_id: str) -> str:
    return os.path.join(get_accounts_root(), account_id)


def get_log_dir() -> str:
    """Return the directory for runtime diagnostic logs (no user content)."""
    path = os.path.join(get_base_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def get_settings_path() -> str:
    """Application-wide settings (language, theme, active account)."""
    return os.path.join(get_base_dir(), "app_settings.json")


def get_registry_path() -> str:
    """Account registry: a list of account metadata records (no passwords)."""
    return os.path.join(get_base_dir(), "accounts.json")


DEFAULT_SETTINGS = {
    "language": None,        # 'fa' | 'en' | None (first run)
    "theme": "light",        # 'light' | 'dark'
    "active_account_id": None,
}


def _load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def load_settings() -> dict:
    """Load application settings, adopting the 1.0 settings file once."""
    settings = dict(DEFAULT_SETTINGS)
    saved = _load_json(get_settings_path())
    if isinstance(saved, dict):
        settings.update(saved)
        return settings
    legacy = _load_json(os.path.join(get_legacy_data_dir(), "settings.json"))
    if isinstance(legacy, dict) and legacy.get("language") in ("fa", "en"):
        settings["language"] = legacy["language"]
        if legacy.get("theme") in ("light", "dark"):
            settings["theme"] = legacy["theme"]
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


def get_webview2_runtime_dir():
    """Return the bundled Fixed Version WebView2 runtime directory or None.

    The runtime ships inside the application folder (never with user data)
    as 'webview2_runtime\\Microsoft.WebView2.FixedVersionRuntime.<ver>.x64'
    with msedgewebview2.exe at its root. When it is absent — for example when
    running straight from source — None is returned and the system-installed
    WebView2 runtime is used instead.
    """
    root = os.path.join(_packaged_base(), "webview2_runtime")
    if not os.path.isdir(root):
        return None
    try:
        for name in sorted(os.listdir(root)):
            candidate = os.path.join(root, name)
            if os.path.exists(os.path.join(candidate, "msedgewebview2.exe")):
                return candidate
    except OSError:
        pass
    return None
