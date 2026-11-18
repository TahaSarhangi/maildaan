"""Lightweight GitHub release updater for MailDaan."""
import hashlib
import json
import logging
import os
import subprocess
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
        self._load_last_check()

    def _load_last_check(self):
        try:
            self.last_check = float(config.load_settings().get("updates_last_check", 0) or 0)
        except (TypeError, ValueError):
            self.last_check = 0

    def _save_last_check(self):
        settings = config.load_settings()
        settings["updates_last_check"] = self.last_check
        config.save_settings(settings)

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
        if not self.download_path or not os.path.exists(self.download_path):
            return {"error": "installer not verified"}
        if self.api.sync_running():
            self.api.cancelSync()
            self.api.sync_thread.join(timeout=30)
        subprocess.Popen([self.download_path], close_fds=True)
        self.api.exitApp()
        return {"ok": True}
