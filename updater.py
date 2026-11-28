"""Lightweight GitHub release updater for MailDaan."""
import ctypes
import hashlib
import json
import logging
import os
import tempfile
import threading
import urllib.request

import config

LOG = logging.getLogger("maildaan.updater")
API_URL = "https://api.github.com/repos/TahaSarhangi/maildaan/releases/latest"
CHECK_INTERVAL = 24 * 60 * 60


def _version(value):
    try:
        return tuple(int(x) for x in str(value).lstrip("vV").split(".")[:3])
    except (TypeError, ValueError):
        return (0, 0, 0)


def _launch_installer(path):
    """Start the verified installer elevated and detached from this process.

    The MailDaan installer (Inno Setup) runs with an asInvoker manifest and
    requests elevation itself, so it must be started the same way Explorer
    starts a double-clicked program: through the shell, not CreateProcess.
    ShellExecuteW("runas") raises the UAC prompt the installer needs and the
    new process is owned by the shell, so it survives MailDaan's exit and is
    never killed when the application closes. A bare subprocess.Popen call
    cannot trigger that prompt, leaving the app closed and nothing installed.

    Raises OSError when the user declines the prompt or the file cannot be
    started, so the caller keeps the application open and reports the failure.
    """
    sw_shownormal = 1
    error_cancelled = 1223  # the user declined the elevation prompt
    shell32 = ctypes.windll.shell32
    # HINSTANCE is pointer-sized: without an explicit return type a 64-bit
    # build would truncate the handle and misread it as an error code.
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    full = os.path.abspath(path)
    handle = shell32.ShellExecuteW(None, "runas", full, None,
                                   os.path.dirname(full), sw_shownormal)
    code = int(handle or 0)
    if code <= 32 or code == error_cancelled:
        raise OSError("ShellExecuteW returned %d" % code)


class UpdateManager:
    def __init__(self, api):
        self.api = api
        self._lock = threading.Lock()
        self.release = None
        self.download_path = None
        self.download_error = ""
        self.download_cancel = None
        self.downloading = False
        self.progress = {"running": False, "downloaded": 0, "total": 0, "percent": 0}
        self.last_check = 0
        # Guards install() against a second hand-off while one is already in
        # progress: two rapid "install" requests must never launch two
        # elevated installers. Cleared again only when the hand-off failed,
        # so a genuine retry stays possible.
        self._installing = False
        self._install_lock = threading.Lock()
        self._load_last_check()

    def _load_last_check(self):
        try:
            self.last_check = float(config.load_settings().get("updates_last_check", 0) or 0)
        except (TypeError, ValueError):
            self.last_check = 0

    def _save_last_check(self):
        try:
            config.update_settings(
                lambda s: s.update(updates_last_check=self.last_check))
        except Exception:
            LOG.info("could not record the last update-check time")

    def should_check(self):
        return (not self.last_check or __import__("time").time() - self.last_check >= CHECK_INTERVAL)

    def check(self, force=False):
        if not force and not self.should_check():
            return self.result()
        self.last_check = __import__("time").time()
        self._save_last_check()
        try:
            req = urllib.request.Request(API_URL, headers={"User-Agent": "MailDaan/" + config.APP_VERSION, "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=15) as response:
                release = json.loads(response.read().decode("utf-8"))
            tag = release.get("tag_name", "")
            if release.get("draft") or release.get("prerelease") or _version(tag) <= _version(config.APP_VERSION):
                self.release = None
            else:
                self.release = release
            return self.result()
        except Exception as exc:
            LOG.info("update check unavailable: %s", exc)
            return {"available": False, "offline": True}

    def result(self):
        if not self.release:
            return {"available": False}
        return {"available": True, "version": self.release.get("tag_name", ""), "notes": (self.release.get("body") or "")[:1200], "url": self.release.get("html_url", ""), "downloaded": bool(self.download_path), "download_error": self.download_error}

    def _asset(self):
        if not self.release:
            return None
        assets = self.release.get("assets") or []
        installer = next((a for a in assets if str(a.get("name", "")).lower().endswith(".exe")), None)
        checksum = next((a for a in assets if "sha256" in str(a.get("name", "")).lower() or "checksum" in str(a.get("name", "")).lower()), None)
        expected = None
        if checksum:
            try:
                with urllib.request.urlopen(urllib.request.Request(checksum.get("browser_download_url"), headers={"User-Agent": "MailDaan"}), timeout=20) as r:
                    text = r.read().decode("utf-8", "replace")
                expected = next((x.lower() for x in text.split() if len(x) == 64 and all(c in "0123456789abcdefABCDEF" for c in x)), None)
            except Exception:
                expected = None
        return installer, expected

    def start_download(self):
        if self.downloading:
            return {"ok": True}
        asset = self._asset()
        if not asset or not asset[0] or not asset[1]:
            self.download_error = "checksum unavailable"
            return {"error": self.download_error}
        self.download_cancel = threading.Event()
        self.downloading = True
        self.download_error = ""
        self.progress = {"running": True, "downloaded": 0, "total": asset[0].get("size") or 0, "percent": 0}
        threading.Thread(target=self._download, args=asset, daemon=True).start()
        return {"ok": True}

    def _download(self, installer, expected):
        path = os.path.join(tempfile.gettempdir(), installer.get("name") or "MailDaan-update.exe")
        tmp = path + ".part"
        try:
            req = urllib.request.Request(installer["browser_download_url"], headers={"User-Agent": "MailDaan/" + config.APP_VERSION, "Accept": "application/octet-stream"})
            digest = hashlib.sha256()
            with urllib.request.urlopen(req, timeout=60) as response, open(tmp, "wb") as fh:
                total = int(response.headers.get("Content-Length") or installer.get("size") or 0)
                done = 0
                while True:
                    if self.download_cancel.is_set():
                        raise InterruptedError()
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    fh.write(chunk); digest.update(chunk); done += len(chunk)
                    self.progress = {"running": True, "downloaded": done, "total": total, "percent": round(done * 100 / total, 1) if total else 0}
            if digest.hexdigest().lower() != expected.lower():
                raise ValueError("checksum verification failed")
            self.download_path = path
            os.replace(tmp, path)
        except InterruptedError:
            self.download_error = "cancelled"
            try: os.remove(tmp)
            except OSError: pass
        except Exception as exc:
            self.download_error = str(exc)
            try: os.remove(tmp)
            except OSError: pass
        finally:
            self.downloading = False
            self.progress["running"] = False

    def cancel_download(self):
        if self.download_cancel: self.download_cancel.set()
        return {"ok": True}

    def progress_state(self):
        result = dict(self.progress)
        result["downloaded"] = result.get("downloaded", 0)
        result["path_ready"] = bool(self.download_path)
        result["error"] = self.download_error
        return result

    def install(self):
        with self._install_lock:
            if self._installing:
                # A hand-off is already in progress (double click, or a
                # retry while the elevation prompt is still up): never start
                # a second installer.
                return {"error_key": "update.alreadyInstalling"}
            self._installing = True
        if not self.download_path or not os.path.exists(self.download_path):
            self._installing = False
            return {"error": "installer not verified"}
        if self.api.sync_running():
            # Never interrupt an active synchronization: an update is only
            # applied with the user's explicit consent, and never while a
            # backup is in progress. The verified installer stays cached so
            # the user can install once the synchronization has finished or
            # been cancelled.
            self._installing = False
            return {"error_key": "update.syncRunning"}
        try:
            _launch_installer(self.download_path)
        except OSError as exc:
            # The elevation request was declined or the installer could not
            # be started: keep MailDaan open and say so instead of closing
            # silently with nothing installed.
            LOG.error("could not start the verified update installer: %s", exc)
            self._installing = False
            return {"error_key": "update.launchFailed"}
        # Remember the version handed over to the installer. The next MailDaan
        # start compares its own APP_VERSION against this value and reports
        # success only on an exact match; a cancelled or failed install, or
        # any other version, leaves no stale record and no false message.
        self._save_pending_update((self.release or {}).get("tag_name", ""))
        self.api.exitApp()
        return {"ok": True}

    def _save_pending_update(self, version):
        try:
            config.update_settings(
                lambda s: s.update(pending_update_version=version))
        except Exception:
            LOG.info("could not record the pending update version")
