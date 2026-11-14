# MailDaan

**MailDaan** (میل‌دان) is a lightweight, bilingual (Persian/English), **offline email archive and explorer** for Windows.

It connects to an IMAP account once, downloads every accessible message and attachment into a local archive (SQLite + original `.eml` files), and then lets you browse, search, filter and export your email — completely offline.

- Developer: **Titanium** — https://github.com/TahaSarhangi
- Version: 1.1.2
- License: MIT

## Features

- **Complete IMAP archiving** — discovers every selectable folder (Inbox, Sent, Drafts, Spam, custom and nested folders), downloads full message content, attachments, and original `.eml` files.
- **Multiple email accounts** — archive several IMAP accounts side by side. Each account keeps its own independent archive (separate SQLite database, `.eml` files, attachments, folders and sync checkpoints), and switching accounts from the sidebar instantly loads that account's data.
- **Professional desktop interface** — persistent navigation sidebar, compact top toolbar, three-pane archive explorer (folders / message list / message detail), light and dark themes with sun/moon switching that persists between launches, and a consistent bundled SVG icon set.
- **Offline browsing** — read messages, view and save attachments, no internet connection required after the first sync.
- **Incremental sync with recovery** — uses IMAP UID + UIDVALIDITY checkpoints. An interrupted sync resumes where it stopped; a failed batch never advances the checkpoint.
- **Bilingual interface** — Persian (RTL, Vazirmatn font) and English (LTR), switchable at any time. First launch asks for your language.
- **Search, filter and sort** — fast SQLite FTS5-backed search across senders, recipients, subjects and bodies, with Persian character normalization (ی/ي, ک/ك, diacritics, ZWNJ), keyboard-layout correction (Persian↔English), typo-tolerant "did you mean …?" suggestions, date and folder filters, and pagination.
- **Optional application password** — protect access to MailDaan with a password (Argon2id hash, never stored in plaintext). When enabled, MailDaan asks for it after every start. This is an application access lock; it does not encrypt the archive, and there is no password reset bypass.
- **Deduplication** — the same message stored in several folders appears once, with all folder associations preserved.
- **Optional Excel reports** — export the current view (or the whole archive) to a formatted workbook with Messages, Contacts and Organizations sheets. Never generated automatically.
- **Private by design** — your archive lives in `%LOCALAPPDATA%\MailDaan`, never inside the application folder, and your password is never stored.

## Windows requirements

- Windows 10 (version 1809 or later) or Windows 11, **64-bit (x64)**
- Microsoft Edge WebView2 runtime — not needed as a separate download: the offline
  installer embeds the official Microsoft Edge WebView2 Evergreen Standalone Offline
  Installer (x64) and installs it automatically if the runtime is missing
- The .NET Framework 4.6.2 runtime used by the embedded pywebview/pythonnet window is
  already part of every supported Windows 10/11 build, so no separate installation is
  ever required; the installer verifies it and reports clearly if it is unavailable

## Download and run

1. Download **`MailDaan-Setup-1.1.2.exe`** — a single, fully self-contained offline
   installer — from [Releases](https://github.com/TahaSarhangi/maildaan/releases).
2. Run it. If your Windows is missing the WebView2 runtime, the required Microsoft
   runtime is installed from inside the setup file itself — **no internet connection
   and no separate downloads are needed at any point**.
3. Launch **MailDaan** from the Start Menu (an optional desktop shortcut is offered).

The old one-folder ZIP (`MailDaan-1.1.2.zip`) remains available for portable use; it
requires WebView2 to already be installed on the machine.

No Python installation or command-line usage is required. Your archive is stored separately in `%LOCALAPPDATA%\MailDaan` and survives updates.

## Configure IMAP

On first launch MailDaan asks for:

- **IMAP Server** (for example `imap.gmail.com`)
- **IMAP Port** (default `993`, IMAP over TLS)
- **Email address / username**
- **Password**

The password is used only for the active synchronization and is never written to disk. (For Gmail, generate an app password and enable IMAP in Gmail settings.)

## Synchronize emails

- The first synchronization downloads every accessible folder.
- Progress, speed and estimated time are shown inside the application window.
- Use **Sync Emails** («به‌روزرسانی ایمیل‌ها») at any time to download only new messages.
- You can cancel at any time; completed downloads are kept and the next sync resumes from the checkpoint.

## Browse offline

After the first sync, MailDaan opens directly into your local archive — no network connection needed. Select folders, search, filter by date or participant, open messages, and save attachments or the original `.eml` file.

## Export to Excel

Choose **Export to Excel** («خروجی اکسل»). The workbook is generated only after you confirm, and covers the current search/filter scope (or the whole archive when no filters are active). Sheets: Messages, Contacts, Organizations.

## Run from source

```bat
git clone https://github.com/TahaSarhangi/maildaan.git
cd maildaan
python -m pip install -r requirements.txt
run-dev.bat
```

Requires Python 3.11+ on Windows.

## Build the executable

```bat
python -m pip install -r requirements-build.txt
build.bat
```

This produces `dist\MailDaan\MailDaan.exe` (one-folder distribution) and `dist\MailDaan-1.1.2.zip` for GitHub Releases.

## Build the fully offline installer

```bat
build-installer.bat
```

`build-installer.ps1` (wrapped by the batch file) performs the whole pipeline:

1. runs PyInstaller (one-folder build),
2. scans the packaged tree to guarantee it contains **no user data** (no databases, `.eml` files, account settings or credentials),
3. downloads and verifies (Authenticode signature from Microsoft + metadata + SHA-256) the official **Microsoft Edge WebView2 Evergreen Standalone Offline Installer (x64)** — the build fails if it cannot be obtained,
4. compiles `installer\maildaan.iss` with Inno Setup (installed locally under `tools\` if needed) into a single self-contained setup executable that embeds the application *and* the offline runtime installer,
5. verifies the produced setup and its file manifest.

Result: **`dist\MailDaan-Setup-1.1.2.exe`** — the only file a Windows x64 user needs. During installation it:

- detects the WebView2 runtime and, when it is missing, extracts and silently executes the embedded official offline installer from the setup's temporary directory (never the online bootstrapper, never any download),
- detects the .NET Framework 4.6.2+ required by pythonnet (already part of every supported Windows 10/11) and installs the bundled official offline redistributable only when it is genuinely missing,
- installs the complete application (Python runtime, all native dependencies, UI assets, Vazirmatn fonts, icons, localizations) to `Program Files`,
- creates Start Menu shortcuts, offers an optional desktop shortcut, registers a standard uninstaller, and can launch MailDaan when setup finishes,
- never touches `%LOCALAPPDATA%\MailDaan` — email archives, SQLite databases, attachments, account and application settings survive installation, upgrade and uninstallation.

## Data location

All user data is stored in `%LOCALAPPDATA%\MailDaan\` — completely separate from the application:

- `app_settings.json` — language, theme, active account
- `app_password.json` — Argon2id hash of the optional application password (never the plaintext password)
- `accounts.json` — account registry (no passwords are ever stored)
- `accounts\<id>\` — per-account independent archives (`archive.db` including its search index, `eml\`, `attachments\`)
- `data\` — a MailDaan 1.0 single-account archive is adopted in place and kept untouched

Upgrading from 1.0 is automatic: the existing archive is registered as the first account and continues to work without re-downloading anything.

## License

MIT — see [LICENSE](LICENSE). The bundled Vazirmatn font is licensed under the SIL Open Font License 1.1 (see [third-party-licenses](third-party-licenses)).
