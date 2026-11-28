# MailDaan

**MailDaan** (میل‌دان) is a free, open-source **offline email archive and explorer** for
Windows. It **supports Persian and English**, connects to **compatible IMAP accounts**, downloads every
accessible message and attachment into a fully local archive, and then lets you browse,
search and export your email — **completely offline**.

**Language:** [English](README.md) · [فارسی (Persian)](README.fa.md)

- **Developer:** Titanium — https://github.com/TahaSarhangi
- **Version:** 1.4.2
- **License:** MIT (see [LICENSE](LICENSE))

## Why MailDaan

Your email stays yours. MailDaan copies your messages into a **local archive on your own
machine** — after that, reading, searching and exporting your email needs **no internet
connection at all**. There is no cloud, no account on any third-party service.

An internet connection is required **only to download new email** from your IMAP server
and to check for application updates. Installing the application, and browsing or searching
an existing archive, never needs one.

## Main features

- **Complete IMAP archiving** — discovers every selectable folder (Inbox, Sent, Drafts,
  Spam, custom and nested folders) and downloads full message content, attachments and the
  original `.eml` files.
- **Multiple independent accounts** — archive several IMAP accounts side by side. Each
  account keeps its own separate archive: its own SQLite database, `.eml` files,
  attachments, folders, sync checkpoints and TLS trust settings.
- **Incremental synchronization** — IMAP `UID` + `UIDVALIDITY` checkpoints make every sync
  download only what is new. An interrupted sync resumes exactly where it stopped, and a
  failed batch never advances the checkpoint.
- **Sync with or without attachments** — download a folder's messages first and fetch their
  attachments later, on demand for one message or a whole selection at a time. MailDaan
  never auto-downloads attachments you did not ask for.
- **Built-in update manager** — the small update icon in the sidebar checks GitHub for new
  releases, lights up a badge when one is available, shows the download as a circular
  progress ring with a live percentage, verifies the installer against its published
  SHA-256 checksum, and then asks explicitly before installing: **Install now** or
  **Remind me later**. The moment you choose **Install now**, a progress card appears
  immediately — naming the version being installed and explaining that MailDaan will
  close and reopen by itself — before the installer is ever launched, and the action can
  never be triggered twice at once. It never installs while a synchronization is running
  and never cancels one without your consent.
- **Clear bilingual errors** — every failure (connection, IMAP, certificate, export, …)
  is reported as a concrete, human-readable message in Persian or English instead of a
  raw technical error.
- **Intelligent offline search** — a fast SQLite FTS5 index across senders, recipients,
  subjects and bodies, with Persian character normalization (ی/ي, ک/ك, diacritics, ZWNJ),
  keyboard-layout correction (typing a Persian word on an English layout still finds it,
  and vice versa) and typo-tolerant *"Did you mean …?"* suggestions, plus date, folder and
  participant filters.
- **Bilingual dates** — message dates are shown in the **Persian Jalali** calendar with
  Persian digits in the Persian interface, and in the **Gregorian** calendar in English.
- **Professional desktop interface** — persistent sidebar, three-pane archive explorer
  (folders / message list / message detail), light and dark themes, bundled Vazirmatn font
  for the Persian UI, switchable between Persian (RTL) and English (LTR) at any time.
- **Selectable, copyable message text** — the subject, correspondents, date and body of an
  open message are ordinary selectable text: drag to select and copy with Ctrl+C (or the
  context menu) in both Persian (RTL) and English (LTR) layouts, for plain-text and
  HTML messages alike.
- **Single-message PDF/PNG export** — any open message can be saved from its toolbar as a
  **PDF** document or a **PNG** image, laid out for reading with the subject, date,
  sender, recipients (To/Cc when present) and body, including Persian, English and mixed
  right-to-left/left-to-right content. The suggested filename is built from the subject
  and date; everything renders locally, and the original `.eml` export stays available
  alongside it.
- **Customizable Excel reports** — a dedicated report dialog lets you choose the
  purpose (comprehensive, activity, contacts, domains or messages), the exact
  scope (date range, folders, direction, drafts handling and an optional
  keyword), which worksheets and columns to include, and the report language.
  Each report is a formatted workbook with an executive summary, an
  activity-by-month chart, a detailed message list, contact and email-domain
  statistics, and an attachment listing — every sheet derived from that same
  filtered scope, with Persian Jalali dates and a right-to-left layout in the
  Persian report. Reports are never generated automatically — only when you
  ask for one.
- **Optional application password** — protect entry into MailDaan with a password stored
  as an **Argon2id hash** (never in plaintext). This is an application-level access lock
  only — it does **not** encrypt the archive, and there is deliberately no password-reset
  bypass.
- **Private by design** — your archive lives in `%LOCALAPPDATA%\MailDaan`, never inside the
  application folder. Your IMAP password is kept in memory **only** for the active
  synchronization and is never written to disk.

## Windows compatibility

- **Windows 10 (version 1809 or later)** or **Windows 11**, **64-bit (x64) only**
- The Microsoft .NET Framework 4.6.2 runtime used by the desktop window is already part of
  every supported Windows 10/11 build; the installer verifies this and can install the
  bundled official offline redistributable automatically if it is ever genuinely missing.

## Completely offline installer

`MailDaan-Setup-1.4.2.exe` is a single, fully self-contained setup program:

- It embeds the official **Microsoft Edge WebView2 Fixed Version Runtime (x64)** and uses
  that private copy, so setup **never installs, downloads or asks for** a system WebView2
  runtime.
- It contains the complete application: Python runtime, all native dependencies, UI assets,
  Vazirmatn fonts, icons and localizations.
- **No internet connection and no separate downloads are needed at any point** during
  installation.
- It installs to `Program Files`, creates Start Menu shortcuts, offers an optional desktop
  shortcut, registers a standard uninstaller, and **never touches** your archive data.

## Screenshots

These screens were captured in a live environment; any private information visible in them
has been blurred so no user data is exposed.

<p float="left">
  <img src="docs/screenshots/english-interface.jpg" width="420" alt="MailDaan English interface" />
  <img src="docs/screenshots/persian-interface.jpg" width="420" alt="MailDaan Persian interface" />
</p>

<p align="center">
  <img src="docs/screenshots/english-interface-02.jpg" width="940" alt="MailDaan English interface — another view" />
</p>

## Download and install

1. Download **`MailDaan-Setup-1.4.2.exe`** from the
   [Releases](https://github.com/TahaSarhangi/maildaan/releases) page.
2. Run it — no internet connection is required. Verify the download anytime against the
   published `MailDaan-Setup-1.4.2.exe.sha256` checksum file.
3. Launch **MailDaan** from the Start Menu (an optional desktop shortcut is offered).

Your archive is stored separately in `%LOCALAPPDATA%\MailDaan` and survives updates and
uninstallations.

## Basic usage

1. **Choose your language** on first launch (Persian or English; switchable later at any
   time from the sidebar).
2. **Add your email account** — IMAP server (for example `imap.gmail.com`), port
   (default `993`, IMAP over TLS), email address and password. The password is used only
   for that synchronization and is never stored.
   *(For Gmail: if your account uses 2-Step Verification, generate an app password in your
   Google Account security settings. If IMAP access is not enabled, you may need to turn it
   on in Gmail settings.)*
3. **Synchronize** — the first sync downloads every accessible folder. Progress, speed and
   estimated time are shown in the application window. You can cancel at any time;
   completed downloads are kept and the next sync resumes from the checkpoint.
4. **Browse offline** — open folders, read messages, view and save attachments, or save the
   original `.eml` file.
5. **Search** — type a name, subject or any text. Persian/English keyboard-layout mistakes
   and most typos are still found, with *"Did you mean …?"* suggestions.
6. **Create an Excel report** (optional) — open the report dialog, pick the purpose and
   scope (dates, folders, direction, drafts, keyword) and the sheets you want, and
   generate a formatted bilingual workbook with summaries, charts and statistics.

## What's new in 1.4.2

- **Correct PDF image export** — the file produced by *Save as PDF* was severely
  distorted in every PDF reader. The embedded image declared the PNG row predictor,
  but its rows were written unfiltered, so readers applied an inverse PNG-Sub
  transform to already-correct pixels and smeared every page — horizontal
  distortion, repeated gray gradients and wrong colors, in Persian, English and
  mixed content alike. The image data is now written as plain, tightly packed RGB
  rows compressed with Flate and no predictor at all, so every page renders
  exactly as MailDaan drew it, on single-page and multi-page exports.
- **Language preference is preserved** — on some installations the
  language-selection dialog reappeared on every start even though a language had
  already been chosen. The application writes its settings with a
  load → modify → save sequence, and several interface actions (the background
  update check, adding or selecting an account, …) can run that same sequence at
  the very moment the language is chosen, each on its own thread; whichever
  writer finished last silently overwrote the other's change and the stored
  language disappeared, while accounts, archives and every other preference
  remained intact. Every settings change is now applied atomically from the
  file's current contents under a single lock, so a concurrent action can never
  revert another one — the chosen language survives restarts, Windows restarts
  and upgrades. Manual language switching is unchanged, and a valid stored
  preference is never overwritten with defaults.

## What's new in 1.4.1

- **Immediate feedback when installing an update** — after choosing **Install now**, a
  progress card appears instantly showing the version being installed and explaining that
  MailDaan closes and reopens by itself, instead of the window simply freezing until the
  Windows permission prompt appears. The install action can no longer be triggered twice
  at the same time, and if the installer cannot be started (or its permission prompt is
  declined) the application stays open, returns to a usable state, and reports the
  failure in Persian or English — never silently claiming success.
- **Selectable, copyable text in the message viewer** — the subject, sender and
  recipients, date and body of an open message are now ordinary selectable text. Drag to
  select and copy with Ctrl+C or the context menu, for plain-text and HTML messages, in
  Persian (right-to-left), English (left-to-right) and mixed content. This change does
  not affect message-list selection (click, Ctrl-click, Shift-click, checkbox mode) or
  any other control.
- **Save a single message as PDF or PNG** — the open message view now offers **Save as
  PDF** and **Save as PNG** next to the existing `.eml` export. Both produce a readable
  rendering of that one message — subject, date, sender, recipients (To/Cc when present)
  and body — including Persian, English and mixed right-to-left/left-to-right text, with
  a suggested filename built from the subject and date. Everything is rendered locally on
  your own machine.

## What's new in 1.4.0

- **Flexible account removal** — removing an account now offers two clear options. *Remove
  Account* unregisters the account while keeping its local archive (database, `.eml` files,
  attachments) on your computer; the archive is reattached automatically when you add the
  same mailbox again. *Remove Account and All Local Data* additionally deletes the
  account's archive directory from this computer after a second confirmation — the mail
  server is never touched. Removal is blocked while the account is synchronizing.
- **Confirmation before cancelling a synchronization** — cancelling a running
  synchronization now asks first ("Are you sure you want to cancel email synchronization?"
  / "آیا مطمئن هستید که می‌خواهید فرایند همگام‌سازی ایمیل‌ها را لغو کنید؟"), so an
  accidental click never interrupts a long download. Downloaded messages are still kept
  and the next sync resumes from the checkpoint.
- **Close button on the update dialog** — the "you are running the latest version" dialog
  now has its own **Close** button next to *Check Again*, so it no longer has to be
  dismissed through the window.
- **Delete messages from the local archive** — select one or more emails (checkbox mode,
  Ctrl-click, Shift-click for a range, *Select all* / *Clear selection*) and delete them
  from the local archive with a single confirmation. The copies on the mail server are
  never affected, and removed messages are not silently downloaded again by an ordinary
  synchronization. Deleting from a folder view removes only that folder's copy; a message
  that also exists in another folder stays there untouched.
- **More reliable synchronization** — a single broken message can no longer stall or
  partially fail a whole synchronization: failing messages are isolated, retried once in
  the same run when the problem is transient, and otherwise recorded for the next sync
  while all remaining messages keep downloading. The resume checkpoint now advances over
  every message actually completed, so the next synchronization never re-downloads
  successful messages and never skips failed ones.
- **Fixed very long or Persian attachment filenames** — some senders embed a raw
  percent-encoded filename (for example `%D8%A7%DB%8C...`) instead of a properly encoded
  one; the archive kept the unreadable text and, with very long names, could exceed the
  Windows path limit and fail the whole message. Attachment payloads are now stored under
  short deterministic names while the original readable name is kept and used when you
  save the file, and an unreadable single attachment no longer discards the message or the
  other attachments.
- **Clearer end-of-sync report** — the synchronization report now distinguishes new
  messages, already-archived (deduplicated) messages, failed or incomplete messages,
  failed or pending attachments, and messages skipped because they were deleted locally —
  together with a capped, sanitized per-message list of what still needs another
  synchronization.

## What's new in 1.3.1

- **Fixed the updater's installer launch and application relaunch** — the update manager
  now uses the Windows shell to start the verified installer with proper UAC elevation
  (`ShellExecuteW("runas")`). The previous `subprocess.Popen` call could not trigger the
  elevation prompt, causing the application to close without installing anything. MailDaan
  now closes cleanly, the installer runs elevated and detached from the shell, and the
  application restarts automatically after installation completes.
- **One-time bilingual update-success confirmation** — after the restarted application
  verifies that its installed version matches the version handed to the installer, it
  displays a single confirmation dialog ("Update Complete" / "بروزرسانی کامل شد") in the
  active UI language. The record is cleared immediately so the notice can never appear
  more than once. A cancelled or failed installation, or a restart into a different
  version, stays silent — the application never claims success falsely.
- **Application version in the footer** — the current version number now appears in the
  window footer ("Version 1.3.1" / "نسخه ۱.۳.۱"), using Latin digits in the English UI
  and Persian digits in the Persian UI.

## What's new in 1.3.0

- **Professional configurable Excel reports** — purpose presets, exact scope
  selection (date range, folders, direction, drafts, keyword), per-worksheet and
  per-column toggles, and a live scope summary with the matching message count.
- **Management summaries and correspondence analysis** — executive-summary
  metrics (totals, incoming/outgoing/drafts, unique correspondents and domains,
  attachment volume, first/last message), activity-by-month charts, and most
  active contacts and domains.
- **Contact and email-domain statistics** — per-contact incoming/outgoing counts,
  activity dates, and separate reporting of custom domains and public email
  providers.
- **Corrected incoming/outgoing metrics** — direction is derived from the
  account's own addresses and aliases, drafts are their own category, and the
  account itself is excluded from correspondent and domain rankings while
  colleagues on the same domain remain included.
- **Jalali dates in Persian reports** — Persian month labels, localized chart
  legends and right-to-left worksheets; English reports stay Gregorian and
  left-to-right.
- **Professional spreadsheet layouts** — frozen header rows with working
  autofilters, locale-aware fonts, and human-readable attachment sizes.
- **Improved placeholders and localized progress timers** — every form
  placeholder and hint now follows the active language, and sync progress,
  elapsed and remaining time are displayed with locale-aware formatting and
  Persian digits in the Persian interface.

## Run from source

```bat
git clone https://github.com/TahaSarhangi/maildaan.git
cd maildaan
python -m pip install -r requirements.txt
run-dev.bat
```

Requires Python 3.11+ on Windows. To build the executable and the fully offline installer,
run `build.bat` then `build-installer.bat` (see
[build the fully offline installer](#completely-offline-installer) above and
`installer/prerequisites/README.txt`).

## Third-party licenses and attribution

MailDaan is licensed under the **MIT License** — see [LICENSE](LICENSE).

- **Vazirmatn font** — Copyright © 2015 Saber Rastikerdar and contributors, licensed under
  the SIL Open Font License 1.1 (see [third-party-licenses](third-party-licenses)).
- **IMAPClient** — BSD 3-Clause · **openpyxl** — MIT · **pywebview** — BSD 3-Clause ·
  **argon2-cffi** — MIT · **RapidFuzz** — MIT.
- **Microsoft Edge WebView2 Fixed Version Runtime (x64)** — a free Microsoft redistributable
  bundled inside the application folder for fully offline use, under Microsoft's license
  terms (<https://learn.microsoft.com/microsoft-edge/webview2/>). The official .NET Framework
  offline redistributable may also be embedded; see `installer/PREREQUISITES.txt`.

---

**MailDaan** — free, open-source bilingual offline email archiving and exploration for
Windows. **Developed by Titanium** — https://github.com/TahaSarhangi