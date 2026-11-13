"""MailDaan — offline email archive & explorer.

pywebview application with a JavaScript-to-Python bridge. User data lives in
%LOCALAPPDATA%\\MailDaan and is never mixed with application assets.
"""

import datetime as dt
import json
import logging
import os
import shutil
import sys
import threading
import traceback

import webview

import config
import dialogs
import export
import imap_sync
import storage

logger = logging.getLogger("maildaan")


def setup_logging():
    try:
        os.makedirs(config.get_log_dir(), exist_ok=True)
        log_path = os.path.join(config.get_log_dir(), "maildaan.log")
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        )
        logging.getLogger("webview").addHandler(
            logging.FileHandler(log_path, encoding="utf-8"))
    except OSError:
        logging.basicConfig(level=logging.CRITICAL)


class AppApi:
    """Bridge API exposed to the frontend as window.pywebview.api."""

    def __init__(self):
        self.settings = config.load_settings()
        self.sync_state = None
        self.cancel_event = None
        self.sync_thread = None
        self.last_report = None
        self._password = ""  # in-memory only, cleared when sync ends
        self._lock = threading.Lock()

    # ------------------------------------------------------------ lifecycle

    def getInitialState(self):
        self.settings = config.load_settings()
        logger.info("frontend bootstrap: interface requested initial state")
        return {
            "language": self.settings.get("language"),
            "configured": bool(self.settings.get("configured")),
            "username": self.settings.get("username", ""),
            "settings": self.settings,
            "version": config.APP_VERSION,
        }

    def setLanguage(self, lang):
        if lang not in ("fa", "en"):
            return {"error": "invalid language"}
        self.settings["language"] = lang
        config.save_settings(self.settings)
        return {"ok": True}

    def getSettings(self):
        self.settings = config.load_settings()
        return self.settings

    def saveImapSettings(self, host, port, username, password):
        try:
            port = int(port)
        except (TypeError, ValueError):
            return {"error_key": "config.invalidPort"}
        if not host or not username:
            return {"error_key": "config.fillAllFields"}
        self.settings = config.load_settings()
        self.settings["imap_host"] = host
        self.settings["imap_port"] = port
        self.settings["username"] = username
        self.settings["configured"] = True
        config.save_settings(self.settings)
        if password:
            self._password = password
        return {"ok": True}

    # ------------------------------------------------------------- syncing

    def sync_running(self):
        return bool(self.sync_thread and self.sync_thread.is_alive())

    def startSync(self, password):
        if self.sync_running():
            return {"error_key": "sync.statusRunning"}
        settings = config.load_settings()
        host = settings.get("imap_host")
        username = settings.get("username")
        port = settings.get("imap_port") or 993
        if not host or not username or not password:
            return {"error_key": "config.fillAllFields"}
        self._password = password
        self.cancel_event = threading.Event()
        self.sync_state = imap_sync.SyncState()
        self.last_report = None
        engine = imap_sync.SyncEngine(host, port, username, password,
                                      self.sync_state, self.cancel_event)
        self.sync_thread = threading.Thread(target=self._run_sync, args=(engine,),
                                            name="maildaan-sync", daemon=True)
        self.sync_thread.start()
        return {"ok": True}

    def _run_sync(self, engine):
        try:
            self.last_report = engine.run()
        except Exception:
            logger.error("sync engine crashed\n%s", traceback.format_exc())
            self.last_report = {"status": "error", "errors": 1,
                                "last_error": "internal error",
                                "new_messages": 0, "deduped": 0,
                                "elapsed": 0, "folders_processed": 0}
        finally:
            self._password = ""
            report = self.last_report or {}
            try:
                payload = json.dumps(report, ensure_ascii=False)
                for window in webview.windows:
                    window.evaluate_js(
                        "window.maildaan && maildaan.pushReport(" +
                        payload.replace("\\", "\\\\").replace("'", "\\'") + ")")
            except Exception:
                pass

    def getSyncProgress(self):
        if self.sync_state:
            return self.sync_state.snapshot()
        return {"running": False, "status": "idle", "total_pending": 0,
                "total_processed": 0, "overall_percent": 0}

    def cancelSync(self):
        if self.cancel_event:
            self.cancel_event.set()
        return {"ok": True}

    def exitApp(self, force=False):
        if self.sync_running():
            if self.cancel_event:
                self.cancel_event.set()
            self.sync_thread.join(timeout=30)
        for window in webview.windows:
            try:
                window.destroy()
            except Exception:
                pass
        return {"ok": True}

    # -------------------------------------------------------------- archive

    def getStats(self):
        with storage.ArchiveDB() as db:
            return db.archive_stats()

    def listFolders(self):
        with storage.ArchiveDB() as db:
            return db.list_folders()

    def queryMessages(self, filters_json, sort="newest", page=1, page_size=50):
        filters = {}
        try:
            if filters_json:
                filters = json.loads(filters_json)
        except ValueError:
            filters = {}
        with storage.ArchiveDB() as db:
            total = db.count_messages(filters)
            rows = db.query_messages(filters, sort, int(page or 1), int(page_size or 50))
            return {"total": total, "page": int(page or 1), "rows": rows}

    def getMessage(self, message_id):
        with storage.ArchiveDB() as db:
            msg = db.get_message(int(message_id))
            if not msg:
                return None
            data_dir = config.get_data_dir()
            msg["eml_exists"] = os.path.exists(
                os.path.join(data_dir, msg.get("eml_relpath") or ""))
            for att in msg["attachments"]:
                att["exists"] = os.path.exists(
                    os.path.join(data_dir, att.get("relpath") or ""))
            return msg

    def saveAttachment(self, message_id, attachment_id):
        with storage.ArchiveDB() as db:
            att = db.message_attachment(int(message_id), int(attachment_id))
        if not att:
            return {"error_key": "common.saveFailed"}
        src = os.path.join(config.get_data_dir(), att["relpath"])
        if not os.path.exists(src):
            return {"error_key": "common.saveFailed"}
        target = dialogs.save_dialog("MailDaan", att["filename_safe"] or att["filename"])
        if not target:
            return None
        try:
            shutil.copy2(src, target)
        except OSError as exc:
            return {"error": str(exc)}
        return {"path": target}

    def saveAllAttachments(self, message_id):
        with storage.ArchiveDB() as db:
            msg = db.get_message(int(message_id))
            attachments = msg["attachments"] if msg else []
        if not attachments:
            return {"error_key": "common.saveFailed"}
        target_dir = dialogs.folder_dialog("MailDaan")
        if not target_dir:
            return None
        saved = []
        data_dir = config.get_data_dir()
        for att in attachments:
            src = os.path.join(data_dir, att["relpath"])
            if not os.path.exists(src):
                continue
            dest = os.path.join(target_dir, att["filename_safe"] or att["filename"])
            base, ext = os.path.splitext(dest)
            counter = 1
            while os.path.exists(dest):
                dest = f"{base} ({counter}){ext}"
                counter += 1
            try:
                shutil.copy2(src, dest)
                saved.append(dest)
            except OSError:
                pass
        if not saved:
            return {"error_key": "common.saveFailed"}
        return {"path": target_dir, "count": len(saved)}

    def saveEml(self, message_id):
        with storage.ArchiveDB() as db:
            msg = db.get_message(int(message_id))
        if not msg:
            return {"error_key": "common.saveFailed"}
        src = os.path.join(config.get_data_dir(), msg["eml_relpath"] or "")
        if not os.path.exists(src):
            return {"error_key": "common.saveFailed"}
        from storage import safe_filename
        default = safe_filename(msg.get("subject") or "message", "message")[:120] + ".eml"
        target = dialogs.save_dialog("MailDaan", default, extension="eml")
        if not target:
            return None
        try:
            shutil.copy2(src, target)
        except OSError as exc:
            return {"error": str(exc)}
        return {"path": target}

    # --------------------------------------------------------------- export

    def exportExcel(self, filters_json, sort="newest"):
        filters = {}
        try:
            if filters_json:
                filters = json.loads(filters_json)
        except ValueError:
            filters = {}
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        default = f"maildaan-report-{stamp}.xlsx"
        target = dialogs.save_dialog("MailDaan", default, extension="xlsx")
        if not target:
            return None
        try:
            with storage.ArchiveDB() as db:
                summary = export.generate_workbook(db, filters, sort,
                                                   self.settings.get("language") or "en",
                                                   target)
        except Exception as exc:
            logger.error("excel export failed\n%s", traceback.format_exc())
            return {"error": str(exc)}
        return summary

    # ------------------------------------------------------------ external

    def openExternal(self, url):
        if not url:
            return {"error": "no url"}
        if url != config.DEVELOPER_URL:
            return {"error": "blocked"}
        try:
            os.startfile(url)  # Windows: default browser
        except OSError as exc:
            return {"error": str(exc)}
        return {"ok": True}


def log_startup_exception(exc):
    """Write a startup failure traceback to %LOCALAPPDATA%\\MailDaan\\logs.

    The packaged executable has no console and pywebview discards stderr,
    so otherwise these errors would be invisible. No user data is logged.
    """
    try:
        os.makedirs(config.get_log_dir(), exist_ok=True)
        path = os.path.join(config.get_log_dir(), "startup.log")
        with open(path, "a", encoding="utf-8") as fh:
            stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            fh.write(f"\n=== {stamp} startup failure ===\n")
            traceback.print_exc(file=fh)
    except OSError:
        pass


def show_webview2_error(lang):
    """Last-resort dialog when the WebView2 runtime is unavailable."""
    try:
        import ctypes
        message_key = "webView2Missing"
        try:
            locale_path = os.path.join(config.get_ui_dir(), "locales",
                                       (lang or "en") + ".json")
            with open(locale_path, "r", encoding="utf-8") as fh:
                message = json.load(fh).get("common", {}).get(message_key, "")
        except (OSError, ValueError):
            message = ""
        if not message:
            message = ("MailDaan requires the Microsoft Edge WebView2 runtime. "
                       "Download it from https://developer.microsoft.com/microsoft-edge/webview2/")
        ctypes.windll.user32.MessageBoxW(0, message, "MailDaan", 0x10)
    except Exception:
        pass


def main():
    setup_logging()
    logger.info("MailDaan %s starting", config.APP_VERSION)
    settings = config.load_settings()

    icon_path = os.path.join(config.get_asset_dir(), "maildaan.ico")
    if not os.path.exists(icon_path):
        icon_path = None

    # Point at the entry HTML file itself: a directory URL resolves to the
    # server root "/", which pywebview's static route cannot serve
    # (asset() requires a file argument and fails with HTTP 500).
    index_path = os.path.join(config.get_ui_dir(), "index.html")
    if not os.path.exists(index_path):
        log_startup_exception(FileNotFoundError(index_path))
        show_webview2_error(settings.get("language"))
        sys.exit(1)

    api = AppApi()
    try:
        window = webview.create_window(
            "MailDaan",
            url=index_path,
            js_api=api,
            width=1280,
            height=840,
            min_size=(960, 600),
            confirm_close=False,
        )

        def on_closing():
            if api.sync_running():
                try:
                    window.evaluate_js(
                        "window.maildaan && maildaan.handleExitRequest();")
                except Exception:
                    pass
                return False  # JS will call exitApp() after confirmation
            return True

        window.events.closing += on_closing

        webview.start(debug=False, http_server=True, icon=icon_path)
    except Exception:
        log_startup_exception(sys.exc_info()[1])
        show_webview2_error(settings.get("language"))
        sys.exit(1)

    # Window closed — stop workers and finish cleanly.
    if api.sync_running():
        if api.cancel_event:
            api.cancel_event.set()
        api.sync_thread.join(timeout=30)
    logging.shutdown()


if __name__ == "__main__":
    main()
