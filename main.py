"""MailDaan — offline email archive & explorer.

pywebview application with a JavaScript-to-Python bridge. User data lives in
%LOCALAPPDATA%\\MailDaan and is never mixed with application assets.
"""

import datetime as dt
import functools
import hashlib
import json
import logging
import os
import shutil
import ssl
import sys
import threading
import time
import traceback

import webview

import accounts
import applock
import config
import dialogs
import export
import imap_sync
import imaptls
import search
import storage
import updater

logger = logging.getLogger("maildaan")

# While the application is locked, sensitive bridge methods return an error
# instead of running. Lock enforcement happens here in Python — not only by
# hiding the UI — so no backend operation can be reached before the user
# authenticates. Requests that must stay available while locked (unlock,
# settings of the interface itself, exiting) are left undecorated.
def requires_unlock(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        if self.lock.enabled and not self.unlocked:
            return {"error_key": "lock.locked"}
        return method(self, *args, **kwargs)
    return wrapper


def user_facing(method):
    """Bridge wrapper: a failed operation must never die silently.

    The traceback is written to the application log and the frontend
    receives a translated error key it can report — no raw tracebacks,
    tokens or credentials cross the bridge. Methods that legitimately
    return None on user cancellation are unaffected (no exception raised).
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception:
            logger.error("%s failed\n%s", method.__name__, traceback.format_exc())
            return {"error_key": "common.internalError"}
    return wrapper


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
        # One-time, non-destructive adoption of a MailDaan 1.0 archive.
        try:
            legacy = accounts.adopt_legacy_account()
            if legacy:
                logger.info("adopted legacy single-account archive for %s",
                            legacy.get("email") or "(unknown)")
        except Exception:
            logger.error("legacy adoption failed\n%s", traceback.format_exc())
        self.settings = config.load_settings()
        self.lock = applock.AppLock()
        self.unlocked = not self.lock.enabled
        self.sync_state = None
        self.cancel_event = None
        self.sync_thread = None
        self.sync_account_id = None
        self.last_report = None
        self.bootstrapped = False  # set by getInitialState (frontend live)
        self._password = ""  # in-memory only, cleared when sync ends
        self._lock = threading.Lock()
        self.updater = updater.UpdateManager(self)

    # -------------------------------------------------------- application lock

    def _locked(self):
        return self.lock.enabled and not self.unlocked

    def getInitialState(self):
        self.settings = config.load_settings()
        registry = [accounts.public_account(a) for a in accounts.all_accounts()]
        active = self._resolve_active()
        logger.info("frontend bootstrap: %d account(s) configured", len(registry))
        self.bootstrapped = True
        return {
            "language": self.settings.get("language"),
            "theme": self.settings.get("theme") or "light",
            "accounts": registry,
            "active_account_id": active["id"] if active else None,
            "version": config.APP_VERSION,
            "lock_enabled": self.lock.enabled,
            "locked": self._locked(),
        }

    def unlock(self, password):
        """Verify the application password and unlock this session."""
        if not self.lock.enabled:
            return {"ok": True, "unlocked": True}
        if self.lock.verify(password):
            self.unlocked = True
            return {"ok": True, "unlocked": True}
        return {"error_key": "lock.invalidPassword"}

    def getAppLockState(self):
        return {"enabled": self.lock.enabled}

    @requires_unlock
    def enableAppPassword(self, new_password, confirm):
        if self.lock.enabled:
            return {"error_key": "lock.alreadyEnabled"}
        if not isinstance(new_password, str) or len(new_password) < 8:
            return {"error_key": "lock.tooShort"}
        if new_password != confirm:
            return {"error_key": "lock.mismatch"}
        self.lock.enable(new_password)
        self.unlocked = True
        logger.info("application password enabled")
        return {"ok": True}

    @requires_unlock
    def changeAppPassword(self, current, new_password, confirm):
        if not self.lock.enabled:
            return {"error_key": "lock.notEnabled"}
        if not self.lock.verify(current):
            return {"error_key": "lock.invalidPassword"}
        if not isinstance(new_password, str) or len(new_password) < 8:
            return {"error_key": "lock.tooShort"}
        if new_password != confirm:
            return {"error_key": "lock.mismatch"}
        self.lock.enable(new_password)
        logger.info("application password changed")
        return {"ok": True}

    @requires_unlock
    def disableAppPassword(self, current):
        if not self.lock.enabled:
            return {"error_key": "lock.notEnabled"}
        if not self.lock.verify(current):
            return {"error_key": "lock.invalidPassword"}
        self.lock.disable()
        self.unlocked = True
        logger.info("application password disabled")
        return {"ok": True}

    # -------------------------------------------------------- accounts/themes

    def _resolve_active(self):
        account = accounts.get_account(self.settings.get("active_account_id"))
        if account is None:
            registry = accounts.all_accounts()
            account = registry[0] if registry else None
            if account:
                self.settings["active_account_id"] = account["id"]
                config.save_settings(self.settings)
        return account

    def _active_paths(self):
        account = self._resolve_active()
        if account is None:
            return None, None
        return account, accounts.account_paths(account)

    def setLanguage(self, lang):
        if lang not in ("fa", "en"):
            return {"error": "invalid language"}
        self.settings = config.load_settings()
        self.settings["language"] = lang
        config.save_settings(self.settings)
        return {"ok": True}

    def setTheme(self, theme):
        if theme not in ("light", "dark"):
            return {"error": "invalid theme"}
        self.settings = config.load_settings()
        self.settings["theme"] = theme
        config.save_settings(self.settings)
        return {"ok": True}

    @requires_unlock
    def getAccounts(self):
        return [accounts.public_account(a) for a in accounts.all_accounts()]

    @requires_unlock
    @user_facing
    def addAccount(self, display_name, email, host, port):
        try:
            port = int(port)
        except (TypeError, ValueError):
            return {"error_key": "config.invalidPort"}
        if not host or not email:
            return {"error_key": "config.fillAllFields"}
        if not (1 <= port <= 65535):
            return {"error_key": "config.invalidPort"}
        try:
            account = accounts.create_account(display_name, email, host, port)
        except OSError as exc:
            # The account directory could not be created. The filesystem
            # detail is safe to show; the cause is logged as well.
            logger.warning("account creation failed for %s: %s", email, exc)
            return {"error_key": "accounts.createFailed",
                    "error_detail": str(exc)}
        if not self.settings.get("active_account_id"):
            self.settings["active_account_id"] = account["id"]
            config.save_settings(self.settings)
        return {"ok": True, "account": accounts.public_account(account)}

    @requires_unlock
    @user_facing
    def updateAccount(self, account_id, display_name, email, host, port):
        try:
            port = int(port)
        except (TypeError, ValueError):
            return {"error_key": "config.invalidPort"}
        if not host or not email:
            return {"error_key": "config.fillAllFields"}
        account = accounts.update_account(account_id, display_name=display_name,
                                          email=email, imap_host=host,
                                          imap_port=port)
        if account is None:
            return {"error_key": "accounts.notFound"}
        return {"ok": True, "account": accounts.public_account(account)}

    @requires_unlock
    def removeAccount(self, account_id):
        if self.sync_running() and self.sync_account_id == account_id:
            return {"error_key": "accounts.removeWhileSyncing"}
        removed = accounts.remove_account(account_id)
        if not removed:
            return {"error_key": "accounts.notFound"}
        self.settings = config.load_settings()
        if self.settings.get("active_account_id") == account_id:
            registry = accounts.all_accounts()
            self.settings["active_account_id"] = registry[0]["id"] if registry else None
            config.save_settings(self.settings)
        return {"ok": True}

    @requires_unlock
    def selectAccount(self, account_id):
        if self.sync_running() and self.sync_account_id != account_id:
            return {"error_key": "sync.switchWhileRunning"}
        account = accounts.get_account(account_id)
        if account is None:
            return {"error_key": "accounts.notFound"}
        self.settings = config.load_settings()
        self.settings["active_account_id"] = account_id
        config.save_settings(self.settings)
        return {"ok": True, "account": accounts.public_account(account)}

    # ------------------------------------------------------------- syncing

    def sync_running(self):
        return bool(self.sync_thread and self.sync_thread.is_alive())

    @requires_unlock
    @user_facing
    def startSync(self, account_id, password, with_attachments=True):
        if self.sync_running():
            return {"error_key": "sync.statusRunning"}
        account = accounts.get_account(account_id)
        if account is None:
            return {"error_key": "accounts.notFound"}
        host = account.get("imap_host")
        username = account.get("email")
        port = account.get("imap_port") or 993
        if not host or not account.get("email") or not password:
            return {"error_key": "config.fillAllFields"}
        self.settings = config.load_settings()
        self.settings["active_account_id"] = account_id
        config.save_settings(self.settings)
        self.sync_account_id = account_id
        self._password = password
        self.cancel_event = threading.Event()
        self.sync_state = imap_sync.SyncState()
        self.last_report = None
        # Mark running synchronously so the frontend cannot observe an idle
        # snapshot in the gap between this call returning and the engine
        # thread becoming visible.
        with self.sync_state.lock:
            self.sync_state.running = True
            self.sync_state.status = "running"
            self.sync_state.account_id = account_id
            self.sync_state.account_email = account.get("email") or ""
        paths = accounts.account_paths(account)
        engine = imap_sync.SyncEngine(
            host, port, account["email"], password, self.sync_state,
            self.cancel_event, db_path=paths["db"], data_dir=paths["root"],
            account_id=account_id, account_email=account.get("email") or "",
            account=account, with_attachments=with_attachments)
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
                                "error_key": "common.internalError",
                                "last_error": "internal error",
                                "new_messages": 0, "deduped": 0,
                                "elapsed": 0, "folders_processed": 0,
                                "account_id": self.sync_account_id}
        finally:
            self._password = ""
            account_id = self.sync_account_id
            self.sync_account_id = None
            report = self.last_report or {}
            if account_id:
                try:
                    accounts.update_account(
                        account_id,
                        last_sync_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                        last_sync_status=report.get("status") or "error")
                except Exception:
                    pass
            try:
                payload = json.dumps(report, ensure_ascii=False)
                # Pass the report as a JSON *string*: pushReport() runs
                # JSON.parse() on its argument.
                for window in webview.windows:
                    window.evaluate_js(
                        "window.maildaan && maildaan.pushReport('" +
                        payload.replace("\\", "\\\\").replace("'", "\\'") + "')")
            except Exception:
                pass

    @requires_unlock
    def getSyncProgress(self):
        if self.sync_state:
            snapshot = self.sync_state.snapshot()
            snapshot["sync_account_id"] = self.sync_account_id
            return snapshot
        return {"running": False, "status": "idle", "total_pending": 0,
                "total_processed": 0, "overall_percent": 0,
                "connected": False, "sync_account_id": None}

    @requires_unlock
    def cancelSync(self):
        if self.cancel_event:
            self.cancel_event.set()
        return {"ok": True}

    # ------------------------------------------------------------- TLS trust

    @requires_unlock
    def trustServerCertificate(self, account_id, sha256):
        """Pin the presented server certificate for this account only.

        Binds the user's decision to exactly the fingerprint shown in the
        certificate dialog, so a certificate swapped in the meantime can
        never be trusted silently.
        """
        account = accounts.get_account(account_id)
        if account is None:
            return {"error_key": "accounts.notFound"}
        try:
            imaptls.save_pin(account, str(sha256 or ""))
        except ValueError:
            return {"error_key": "tls.changed"}
        logger.info("pinned server certificate for account %s", account_id)
        return {"ok": True}

    @requires_unlock
    @user_facing
    def importCaCertificate(self, account_id):
        """Import a custom trusted CA certificate (PEM) for this account."""
        account = accounts.get_account(account_id)
        if account is None:
            return {"error_key": "accounts.notFound"}
        source = dialogs.open_dialog("MailDaan", extension="pem")
        if not source:
            return None
        try:
            saved = imaptls.save_ca(account, source)
        except (ssl.SSLError, OSError, ValueError) as exc:
            logger.warning("CA import failed for account %s: %s", account_id, exc)
            return {"error_key": "tls.caFailed",
                    "error_detail": str(exc)}
        logger.info("imported custom CA for account %s", account_id)
        return {"ok": True, "path": saved}

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

    @requires_unlock
    @user_facing
    def getStats(self):
        account, paths = self._active_paths()
        if account is None:
            return {"messages": 0, "total_size": 0, "contacts": 0,
                    "attachments": 0, "organizations": 0, "folders": 0,
                    "account_id": None}
        with storage.ArchiveDB(paths["db"]) as db:
            stats = db.archive_stats()
        stats["account_id"] = account["id"]
        return stats

    @requires_unlock
    @user_facing
    def listFolders(self):
        account, paths = self._active_paths()
        if account is None:
            return []
        with storage.ArchiveDB(paths["db"]) as db:
            return db.list_folders()

    @requires_unlock
    @user_facing
    def queryMessages(self, filters_json, sort="newest", page=1, page_size=50):
        account, paths = self._active_paths()
        if account is None:
            return {"total": 0, "page": int(page or 1), "rows": [],
                    "suggestion": None}
        filters = {}
        try:
            if filters_json:
                filters = json.loads(filters_json)
        except ValueError:
            filters = {}
        with storage.ArchiveDB(paths["db"]) as db:
            return db.search_query(filters, sort, int(page or 1),
                                   int(page_size or 50))

    @requires_unlock
    @user_facing
    def getMessage(self, message_id):
        account, paths = self._active_paths()
        if account is None:
            return None
        with storage.ArchiveDB(paths["db"]) as db:
            msg = db.get_message(int(message_id))
            if not msg:
                return None
            msg["eml_exists"] = os.path.exists(
                os.path.join(paths["root"], msg.get("eml_relpath") or ""))
            for att in msg["attachments"]:
                att["exists"] = os.path.exists(
                    os.path.join(paths["root"], att.get("relpath") or ""))
            return msg

    @requires_unlock
    @user_facing
    def saveAttachment(self, message_id, attachment_id):
        account, paths = self._active_paths()
        if account is None:
            return {"error_key": "common.saveFailed"}
        with storage.ArchiveDB(paths["db"]) as db:
            att = db.message_attachment(int(message_id), int(attachment_id))
        if not att:
            return {"error_key": "common.saveFailed"}
        src = os.path.join(paths["root"], att["relpath"])
        if not os.path.exists(src):
            # The row exists but the payload file does not: the message was
            # archived without attachments. Say so — it is recoverable.
            return {"error_key": "viewer.attachmentMissing"}
        target = dialogs.save_dialog("MailDaan", att["filename_safe"] or att["filename"])
        if not target:
            return None
        try:
            shutil.copy2(src, target)
        except OSError as exc:
            logger.warning("attachment save failed: %s", exc)
            return {"error_key": "common.saveFailed",
                    "error_detail": str(exc)}
        return {"path": target}

    @requires_unlock
    @user_facing
    def saveAllAttachments(self, message_id):
        account, paths = self._active_paths()
        if account is None:
            return {"error_key": "common.saveFailed"}
        with storage.ArchiveDB(paths["db"]) as db:
            msg = db.get_message(int(message_id))
            attachments = msg["attachments"] if msg else []
        if not attachments:
            return {"error_key": "common.saveFailed"}
        target_dir = dialogs.folder_dialog("MailDaan")
        if not target_dir:
            return None
        saved = []
        for att in attachments:
            src = os.path.join(paths["root"], att["relpath"])
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
            # Either no payload file exists at all (attachment-free sync —
            # recoverable) or copying them failed; tell them apart.
            any_on_disk = any(
                os.path.exists(os.path.join(paths["root"], att["relpath"]))
                for att in attachments)
            if any_on_disk:
                return {"error_key": "common.saveFailed"}
            return {"error_key": "viewer.attachmentMissing"}
        return {"path": target_dir, "count": len(saved)}

    @requires_unlock
    @user_facing
    def saveEml(self, message_id):
        account, paths = self._active_paths()
        if account is None:
            return {"error_key": "common.saveFailed"}
        with storage.ArchiveDB(paths["db"]) as db:
            msg = db.get_message(int(message_id))
        if not msg:
            return {"error_key": "common.saveFailed"}
        src = os.path.join(paths["root"], msg["eml_relpath"] or "")
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
            logger.warning("eml save failed: %s", exc)
            return {"error_key": "common.saveFailed",
                    "error_detail": str(exc)}
        return {"path": target}

    @requires_unlock
    @user_facing
    def downloadMissingAttachments(self, message_ids, password=None):
        """Download attachment payloads for previously skipped messages.

        Fetches the full RFC822 message for each partial row from IMAP,
        upgrades the row with attachment metadata and payload files, and
        drops duplicate rows that already have a complete copy.
        """
        account, paths = self._active_paths()
        if account is None:
            return {"error_key": "accounts.none"}
        if not message_ids:
            return {"ok": True, "completed": 0}
        host = account.get("imap_host")
        username = account.get("email")
        port = account.get("imap_port") or 993
        if not host or not username:
            return {"error_key": "config.fillAllFields"}
        if not password:
            return {"error_key": "config.fillAllFields"}
        completed = 0
        engine = imap_sync.SyncEngine(
            host, port, username, password, imap_sync.SyncState(),
            threading.Event(), db_path=paths["db"], data_dir=paths["root"],
            account_id=account.get("id", ""),
            account_email=account.get("email") or "",
            account=account, with_attachments=True)
        # The engine's database handle resolves folder ids to IMAP names.
        # ArchiveDB opens the SQLite file, so it must be closed again.
        engine.db = storage.ArchiveDB(paths["db"])
        try:
            # Partial rows must carry the message id: without it
            # complete_message() could never upgrade the row and the
            # downloaded payload would be silently discarded.
            with storage.ArchiveDB(paths["db"]) as db:
                rows = [dict(r) for r in db.conn.execute(
                    "SELECT m.id AS message_id, mf.folder_id, mf.uid, m.content_hash "
                    "FROM message_folders mf JOIN messages m ON m.id=mf.message_id "
                    "WHERE m.complete=0 AND mf.message_id IN (%s)"
                    % ",".join("?" * len(message_ids)),
                    [int(mid) for mid in message_ids]).fetchall()]
            client = engine._connect()
            try:
                for row in rows:
                    try:
                        client.select_folder(
                            engine._folder_imap_name(row["folder_id"]))
                        fetched = client.fetch([row["uid"]], ["BODY.PEEK[]"])
                        raw = engine._extract_raw(fetched, row["uid"])
                        if not raw:
                            continue
                        content_hash = hashlib.sha256(raw).hexdigest()
                        parsed = imap_sync.parse_message(raw, content_hash)
                        engine._write_files(raw, parsed)
                        with storage.ArchiveDB(paths["db"]) as db:
                            if db.complete_message(row["message_id"], parsed):
                                completed += 1
                    except Exception as exc:
                        logger.warning("attachment download failed for uid %s: %s",
                                       row.get("uid"), exc)
            finally:
                try:
                    client.logout()
                except Exception:
                    pass
        except imap_sync.SyncError as exc:
            logger.error("attachment download failed: %s", exc)
            result = {"error_key": exc.key}
            if exc.detail:
                result["error_detail"] = exc.detail
            return result
        except imaptls.CertificateRejected:
            # Trust decisions are handled by the synchronization flow; here
            # the user gets a plain explanation instead of protocol text.
            logger.error("attachment download failed: untrusted certificate for %s", host)
            return {"error_key": "sync.tlsError"}
        except (OSError, ssl.SSLError) as exc:
            failure = imap_sync._classify_connect_failure(exc, host, port)
            logger.error("attachment download failed: %s", exc)
            result = {"error_key": failure.key}
            if failure.detail:
                result["error_detail"] = failure.detail
            return result
        except Exception:
            logger.error("attachment download failed\n%s", traceback.format_exc())
            return {"error_key": "common.internalError"}
        finally:
            try:
                engine.db.close()
            except Exception:
                pass
        return {"ok": True, "completed": completed}

    # --------------------------------------------------------------- export

    @requires_unlock
    @user_facing
    def exportExcel(self, filters_json, sort="newest"):
        account, paths = self._active_paths()
        if account is None:
            return {"error_key": "export.failed"}
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
            with storage.ArchiveDB(paths["db"]) as db:
                summary = export.generate_workbook(db, filters, sort,
                                                    self.settings.get("language") or "en",
                                                    target)
        except Exception as exc:
            logger.error("excel export failed\n%s", traceback.format_exc())
            return {"error_key": "export.failed",
                    "error_detail": str(exc)}
        return summary

    # ------------------------------------------------------------ external

    @requires_unlock
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

    # ----------------------------------------------------------- updates

    def checkForUpdates(self, force=False):
        return self.updater.check(force=force)

    def getUpdateState(self):
        return self.updater.result()

    def startUpdateDownload(self):
        return self.updater.start_download()

    def cancelUpdateDownload(self):
        return self.updater.cancel_download()

    def updateDownloadProgress(self):
        return self.updater.progress_state()

    def installUpdate(self):
        return self.updater.install()


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


def show_startup_error(lang):
    """Short localized dialog when the application window cannot start.

    The actual error is written to the application log by the caller; the
    dialog never asks the user to download anything (MailDaan ships its own
    WebView2 runtime).
    """
    try:
        import ctypes
        message_key = "startupFailed"
        try:
            locale_path = os.path.join(config.get_ui_dir(), "locales",
                                       (lang or "en") + ".json")
            with open(locale_path, "r", encoding="utf-8") as fh:
                message = json.load(fh).get("common", {}).get(message_key, "")
        except (OSError, ValueError):
            message = ""
        if not message:
            message = ("MailDaan could not start. The cause was recorded in "
                       "the MailDaan log folder.")
        ctypes.windll.user32.MessageBoxW(0, message, "MailDaan", 0x10)
    except Exception:
        pass


def webview2_runtime_present():
    """True when the Edge WebView2 runtime binaries are actually on disk.

    Used only for diagnostics. MailDaan normally uses the Fixed Version
    runtime bundled inside the application folder; this checks whether any
    system-installed Evergreen runtime is also present.
    """
    pf = os.environ.get("ProgramFiles") or r"C:\Program Files"
    pf86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    for base in (pf, pf86, local):
        root = os.path.join(base, "Microsoft", "EdgeWebView", "Application")
        if os.path.isdir(root):
            for name in os.listdir(root):
                if os.path.exists(os.path.join(root, name, "msedgewebview2.exe")):
                    return True
    return False


def configure_webview2_runtime():
    """Point pywebview and the WebView2 loader at the bundled runtime.

    MailDaan ships the official Microsoft WebView2 Fixed Version Runtime
    inside the application folder, so it never depends on a registered
    system-installed Evergreen runtime. Must run before webview.start():
    pywebview evaluates WEBVIEW2_RUNTIME_PATH once while loading its
    Windows backend.
    """
    runtime_dir = config.get_webview2_runtime_dir()
    if not runtime_dir:
        logger.warning(
            "bundled WebView2 Fixed Version runtime not found under %s; "
            "falling back to any system-installed runtime",
            os.path.join(config._packaged_base(), "webview2_runtime"))
        return
    # Microsoft's documented variable for the Fixed Version runtime folder.
    os.environ.setdefault("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", runtime_dir)
    # pywebview's EdgeChromium backend maps this to the WinForms
    # CoreWebView2CreationProperties.BrowserExecutableFolder.
    webview.settings["WEBVIEW2_RUNTIME_PATH"] = runtime_dir
    logger.info("using bundled WebView2 Fixed Version runtime: %s", runtime_dir)


def start_frontend_watchdog(api, timeout=30):
    """Replace a silently blank window with a clear, actionable message.

    WebView2 can fail to initialize after the native window has already
    opened. pywebview only logs the error, leaving a white window with no
    UI. If the frontend has not bootstrapped by `timeout` seconds, log the
    diagnosis (including which runtime was configured) and tell the user
    where to find it.
    """
    def watch():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if api.bootstrapped:
                return
            time.sleep(1)
        if api.bootstrapped:
            return
        logger.error(
            "frontend did not bootstrap within %ss — the WebView2 runtime "
            "failed to initialize (renderer=%s, configured runtime=%s, "
            "system runtime files present=%s)",
            timeout, getattr(webview, "renderer", "unknown"),
            webview.settings.get("WEBVIEW2_RUNTIME_PATH") or "(system)",
            webview2_runtime_present())
        show_startup_error(api.settings.get("language"))

    threading.Thread(target=watch, name="maildaan-watchdog",
                     daemon=True).start()


def main():
    setup_logging()
    logger.info("MailDaan %s starting", config.APP_VERSION)
    settings = config.load_settings()
    configure_webview2_runtime()
    logger.info("webview2 runtime: bundled=%s, system files present=%s",
                webview.settings.get("WEBVIEW2_RUNTIME_PATH") or "(none)",
                webview2_runtime_present())

    icon_path = os.path.join(config.get_asset_dir(), "maildaan.ico")
    if not os.path.exists(icon_path):
        icon_path = None

    # Point at the entry HTML file itself: a directory URL resolves to the
    # server root "/", which pywebview's static route cannot serve
    # (asset() requires a file argument and fails with HTTP 500).
    index_path = os.path.join(config.get_ui_dir(), "index.html")
    if not os.path.exists(index_path):
        log_startup_exception(FileNotFoundError(index_path))
        show_startup_error(settings.get("language"))
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

        start_frontend_watchdog(api)
        webview.start(debug=False, http_server=True, icon=icon_path)
    except Exception:
        log_startup_exception(sys.exc_info()[1])
        show_startup_error(settings.get("language"))
        sys.exit(1)

    # Window closed — stop workers and finish cleanly.
    logger.info("window closed (renderer=%s)", getattr(webview, "renderer", "unknown"))
    if api.sync_running():
        if api.cancel_event:
            api.cancel_event.set()
        api.sync_thread.join(timeout=30)
    logging.shutdown()


if __name__ == "__main__":
    main()
