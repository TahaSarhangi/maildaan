MailDaan bundles third-party libraries and resources. Their licenses are
reproduced or referenced here.

- Vazirmatn font (Persian UI font)
  Copyright (c) 2015 Saber Rastikerdar and contributors.
  License: SIL Open Font License 1.1 — see Vazirmatn-OFL.txt
  Source: https://github.com/rastikerdar/vazirmatn

- IMAPClient (IMAP protocol client)
  License: BSD 3-Clause — https://github.com/mjs/imapclient/blob/master/LICENSE

- openpyxl (Excel workbook generation)
  License: MIT — https://openpyxl.readthedocs.io

- pywebview (desktop window / WebView2 wrapper)
  License: BSD 3-Clause — https://github.com/r0x0r/pywebview

- argon2-cffi (Argon2id password hashing)
  License: MIT — https://github.com/hynek/argon2-cffi

- RapidFuzz (fuzzy string matching for search suggestions)
  License: MIT — https://github.com/rapidfuzz/RapidFuzz

Microsoft Edge WebView2 Fixed Version Runtime (x64)
---------------------------------------------------
MailDaan embeds the official Microsoft Edge WebView2 *Fixed Version Runtime
(x64)* as a free Microsoft redistributable component. It is bundled inside
the application folder (webview2_runtime\) and used through Microsoft's
documented WEBVIEW2_BROWSER_EXECUTABLE_FOLDER / browserExecutableFolder
mechanism, with no system-level installation or registration. The runtime
is obtained during the build from the official Microsoft distribution point
linked from Microsoft's WebView2 download page, and its Microsoft
Authenticode signature is verified before it is embedded. It is licensed by
Microsoft Corporation under the Microsoft Edge WebView2 license terms and
privacy information published at:
  https://learn.microsoft.com/microsoft-edge/webview2/
  https://www.microsoft.com/servicesagreement/
The complete license terms are included by Microsoft inside the runtime
folder itself. The same applies to the optional Microsoft .NET Framework
4.6.2 offline redistributable that may be embedded by the installer (see
../installer/PREREQUISITES.txt).
