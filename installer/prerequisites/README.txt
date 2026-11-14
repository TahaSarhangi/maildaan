Microsoft runtime prerequisites (auto-downloaded by the build, never committed)
=============================================================================

This folder holds the official Microsoft offline runtime installers that
build-installer.ps1 embeds into the MailDaan setup executable. The files are
downloaded from Microsoft's stable distribution points and verified by their
Authenticode signature (Microsoft Corporation) during the build.

 - MicrosoftEdgeWebView2RuntimeInstallerX64.exe
     The Microsoft Edge WebView2 *Evergreen Standalone Offline Installer
     (x64)*, from https://go.microsoft.com/fwlink/?linkid=2124701 .
     MANDATORY for a fully offline installer.

 - NDP462-KB3151800-x86-x64-AllOS-ENU.exe  (optional)
     The Microsoft .NET Framework 4.6.2 offline redistributable. Optional
     because every supported Windows 10/11 build already includes .NET
     Framework 4.7.2 or newer as part of the operating system. If the build
     cannot fetch it, setup still detects .NET Framework 4.6.2+ and reports a
     clear error when it is missing. Drop the official file in this folder
     and it will be embedded and installed automatically when needed.

manifest.json records the SHA-256, size and source URL of each file as it was
verified at build time.
