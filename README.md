# MailDaan

**MailDaan** (میل‌دان) is a lightweight, bilingual (Persian/English), **offline email archive and explorer** for Windows.

It connects to an IMAP account once, downloads every accessible message and attachment into a local archive (SQLite + original `.eml` files), and then lets you browse, search, filter and export your email — completely offline.

- Developer: **Titanium** — https://github.com/TahaSarhangi
- Version: 1.0.0
- License: MIT

## Features

- **Complete IMAP archiving** — discovers every selectable folder (Inbox, Sent, Drafts, Spam, custom and nested folders), downloads full message content, attachments, and original `.eml` files.
- **Offline browsing** — read messages, view and save attachments, no internet connection required after the first sync.
- **Incremental sync with recovery** — uses IMAP UID + UIDVALIDITY checkpoints. An interrupted sync resumes where it stopped; a failed batch never advances the checkpoint.
- **Bilingual interface** — Persian (RTL, Vazirmatn font) and English (LTR), switchable at any time. First launch asks for your language.
- **Search, filter and sort** — fast SQLite search across senders, recipients, subjects and bodies, with Persian character normalization (ی/ي, ک/ك), date and folder filters, and pagination.
- **Deduplication** — the same message stored in several folders appears once, with all folder associations preserved.
- **Optional Excel reports** — export the current view (or the whole archive) to a formatted workbook with Messages, Contacts and Organizations sheets. Never generated automatically.
- **Private by design** — your archive lives in `%LOCALAPPDATA%\MailDaan`, never inside the application folder, and your password is never stored.

## Windows requirements

- Windows 10 or 11 (64-bit)
- Microsoft Edge WebView2 runtime (preinstalled on Windows 11; on Windows 10 download it from https://developer.microsoft.com/microsoft-edge/webview2/)

## Download and run

1. Download the latest `MailDaan-<version>.zip` from [Releases](https://github.com/TahaSarhangi/maildaan/releases).
2. Extract the ZIP to any folder.
3. Double-click **`MailDaan.exe`**.

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

This produces `dist\MailDaan\MailDaan.exe` (one-folder distribution) and `dist\MailDaan-1.0.0.zip` for GitHub Releases.

## Data location

All user data is stored in `%LOCALAPPDATA%\MailDaan\` (SQLite database, `.eml` files, attachments, settings and exports), completely separate from the application.

## License

MIT — see [LICENSE](LICENSE). The bundled Vazirmatn font is licensed under the SIL Open Font License 1.1 (see [third-party-licenses](third-party-licenses)).
