Microsoft runtime prerequisites (auto-downloaded by the build, never committed)
=============================================================================

This folder holds the official Microsoft runtime redistributables that
build-installer.ps1 embeds into the MailDaan setup executable. The files are
downloaded from Microsoft's stable distribution points and verified by their
Authenticode signature (Microsoft Corporation) during the build.

 - Microsoft.WebView2.FixedVersionRuntime.<version>.x64.cab
     The Microsoft Edge WebView2 *Fixed Version Runtime (x64)*, discovered on
     Microsoft's official WebView2 download page
     (https://developer.microsoft.com/en-us/microsoft-edge/webview2/).
     MANDATORY for a fully offline installer. The build extracts it with
     Microsoft's documented `expand` command into ..\..\webview2_runtime\,
     which PyInstaller bundles inside the application folder. MailDaan then
     uses this private runtime via WEBVIEW2_BROWSER_EXECUTABLE_FOLDER /
     pywebview's WEBVIEW2_RUNTIME_PATH instead of any system-installed
     Evergreen runtime.

 - NDP462-KB3151800-x86-x64-AllOS-ENU.exe  (optional)
     The Microsoft .NET Framework 4.6.2 offline redistributable. Optional
     because every supported Windows 10/11 build already includes .NET
     Framework 4.7.2 or newer as part of the operating system. If the build
     cannot fetch it, setup still detects .NET Framework 4.6.2+ and reports
     a clear error when it is missing. Drop the official file in this folder
     and it will be embedded and installed automatically when needed.

manifest.json records the SHA-256, size and source URL of each file as it was
verified at build time.
