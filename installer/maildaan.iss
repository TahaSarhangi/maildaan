; ============================================================================
; MailDaan — fully offline, self-contained Windows x64 installer (Inno Setup)
; ============================================================================
; Emits a SINGLE setup executable that contains:
;   * the complete PyInstaller one-folder distribution of MailDaan
;     (Python runtime, all DLLs/modules/native dependencies, HTML/CSS/JS,
;     icons, localization files and the bundled Vazirmatn fonts),
;   * the official Microsoft Edge WebView2 Fixed Version Runtime (x64),
;     bundled inside the application folder (webview2_runtime\). MailDaan
;     uses this private runtime instead of any system-installed WebView2
;     runtime, so setup never installs, downloads or requires the Evergreen
;     runtime and never asks the user to download anything,
;   * (optionally) the official Microsoft .NET Framework offline
;     redistributable, used only when .NET Framework 4.6.2+ is absent.
;
; No download or network access happens during installation: every runtime
; is embedded inside the setup executable at build time.
;
; User data (%LOCALAPPDATA%\MailDaan: archives, SQLite databases, .eml
; files, attachments, account settings, application settings) is NEVER
; touched, created, overwritten or deleted by setup or uninstall below.
; ============================================================================

#ifndef MyAppVersion
  #define MyAppVersion "1.3.0"
#endif

#define AppIdGuid "{{F8FF4FD1-7339-4E20-B086-F2E1D3428142}"
#ifndef WebView2FixedVersion
  #define WebView2FixedVersion "157.0.4341.60"
#endif
#define WebView2RuntimeDirName "Microsoft.WebView2.FixedVersionRuntime." + WebView2FixedVersion + ".x64"
#define DotNetInstallerName "NDP462-KB3151800-x86-x64-AllOS-ENU.exe"

#ifexist "..\dist\MailDaan\_internal\webview2_runtime\" + WebView2RuntimeDirName + "\msedgewebview2.exe"
#else
  #error The Microsoft Edge WebView2 Fixed Version Runtime (x64) is missing from the build. Run build-installer.bat to download and extract it during the build. A fully offline installer cannot be produced without it.
#endif

#ifexist "prerequisites\" + DotNetInstallerName
  #define HasDotNetInstaller
#endif

#ifndef DotNetSha256
  #define DotNetSha256 ""
#endif

[Setup]
AppName=MailDaan
AppVersion={#MyAppVersion}
AppVerName=MailDaan {#MyAppVersion}
AppId={#AppIdGuid}
AppPublisher=Titanium
AppPublisherURL=https://github.com/TahaSarhangi
AppSupportURL=https://github.com/TahaSarhangi/maildaan/issues
AppUpdatesURL=https://github.com/TahaSarhangi/maildaan/releases
AppContact=https://github.com/TahaSarhangi
Appcopyright=© 2026 MailDaan
AppComments=MailDaan - offline email archive and explorer
AppReadmeFile=https://github.com/TahaSarhangi/maildaan#readme
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany=Titanium
VersionInfoDescription=MailDaan {#MyAppVersion} - offline email archive and explorer
VersionInfoProductName=MailDaan
VersionInfoProductTextVersion={#MyAppVersion}
VersionInfoCopyright=© 2026 MailDaan (MIT License)

DefaultDirName={autopf}\MailDaan
DefaultGroupName=MailDaan
DisableProgramGroupPage=yes
AllowNoIcons=yes
UninstallDisplayIcon={app}\MailDaan.exe
UninstallDisplayName=MailDaan

WizardStyle=modern
SetupIconFile=..\assets\maildaan.ico
LicenseFile=..\LICENSE
InfoBeforeFile=PREREQUISITES.txt

; ---- Architecture: 64-bit Windows only -------------------------------------
; 'x64os' rejects x86 and ARM64 Windows: MailDaan bundles native x64
; binaries (Python, pythonnet, WebView2Loader, the Fixed Version Runtime)
; that are not verified on any other architecture.
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
; Windows 10 1809 is the oldest build that is both still supported and
; supported by the Microsoft Edge WebView2 Runtime; it already includes
; .NET Framework 4.7.2, which satisfies the pythonnet requirement.
MinVersion=10.0.17763

; ---- Privileges ------------------------------------------------------------
; MailDaan installs per-machine into Program Files and, when the .NET
; Framework is missing, may install that machine-wide runtime, which
; genuinely requires elevation. One single UAC prompt at the start
; therefore covers everything; prerequisites are never re-elevated.
PrivilegesRequired=admin
; The only per-user area touched is %LOCALAPPDATA%\MailDaan: its directories
; are pre-created for the installing user (and created on demand by the app
; for any other user). No user data is ever removed by setup or uninstall.
UsedUserAreasWarning=no

; ---- Upgrade housekeeping --------------------------------------------------
; If MailDaan is running during an upgrade, close it (Restart Manager).
CloseApplications=force
CloseApplicationsFilter=MailDaan.exe
RestartApplications=yes

Compression=lzma2/ultra64
SolidCompression=yes
OutputDir=..\dist
OutputBaseFilename=MailDaan-Setup-{#MyAppVersion}
OutputManifestFile=MailDaan-Setup-{#MyAppVersion}.manifest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Dirs]
; Create the per-user application directories up front and never remove
; them (or anything inside them) on uninstall: they hold account settings,
; email archives, SQLite databases, attachments and logs.
Name: "{localappdata}\MailDaan"; Flags: uninsneveruninstall
Name: "{localappdata}\MailDaan\accounts"; Flags: uninsneveruninstall
Name: "{localappdata}\MailDaan\logs"; Flags: uninsneveruninstall

[Files]
; ---- The application (PyInstaller one-folder distribution) ----------------
; This includes the bundled WebView2 Fixed Version Runtime under
; _internal\webview2_runtime\.
Source: "..\dist\MailDaan\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; ---- Attribution -----------------------------------------------------------
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\third-party-licenses\*"; DestDir: "{app}\third-party-licenses"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "PREREQUISITES.txt"; DestDir: "{app}"; Flags: ignoreversion
; ---- Embedded offline .NET installer (never installed as a file) -----------
#ifdef HasDotNetInstaller
Source: "prerequisites\{#DotNetInstallerName}"; Flags: dontcopy
#endif

[InstallDelete]
; On an upgrade, remove the previous PyInstaller internals first so that
; stale native libraries or modules can never be left behind. This only
; ever touches the application folder in Program Files, never user data.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\MailDaan"; Filename: "{app}\MailDaan.exe"; Comment: "Offline email archive and explorer"
Name: "{group}\Uninstall MailDaan"; Filename: "{uninstallexe}"
Name: "{commondesktop}\MailDaan"; Filename: "{app}\MailDaan.exe"; Tasks: desktopicon; Comment: "Offline email archive and explorer"

[Run]
Filename: "{app}\MailDaan.exe"; Description: "{cm:LaunchProgram,MailDaan}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Deliberately empty: user data under %LOCALAPPDATA%\MailDaan (email
; archives, SQLite databases, .eml/attachment files, account and
; application settings) must survive uninstallation. Setup only removes
; the files it installed into {app}.

[Code]
const
  DotNetInstallerName = '{#DotNetInstallerName}';
  DotNetExpectedSha256 = '{#DotNetSha256}';
  WebView2RuntimeDirName = '{#WebView2RuntimeDirName}';
#ifdef HasDotNetInstaller
  DotNetInstallerBundled = True;
#else
  DotNetInstallerBundled = False;
#endif

var
  RestartNeeded: Boolean;

// ---------------------------------------------------------------------------
// Detection: Microsoft .NET Framework 4.6.2+
// ---------------------------------------------------------------------------
// Required at runtime by the bundled pythonnet (Python for .NET) host that
// pywebview uses on Windows. Every supported Windows 10/11 build ships with
// .NET Framework 4.7.2 or newer as an operating-system component.

function DotNetIsSupported: Boolean;
begin
  Result := IsDotNetInstalled(net462, 0);
end;

// ---------------------------------------------------------------------------
// Prerequisite execution helpers
// ---------------------------------------------------------------------------

function PrerequisiteIsIntact(const Path, ExpectedSha256: string): Boolean;
begin
  // The build recorded the official installer's SHA-256 when it embedded it;
  // a mismatch means corruption or tampering and must never be executed.
  if ExpectedSha256 = '' then
    Result := True
  else
    Result := (UpperCase(GetSHA256OfFile(Path)) = UpperCase(ExpectedSha256));
end;

function RunPrerequisiteInstaller(const Path, Parameters: string;
  out ExitCode: Integer): Boolean;
begin
  ExitCode := -1;
  Result := Exec(Path, Parameters, '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
end;

function InstallerResultMessage(const Name: string; ExitCode: Integer): string;
begin
  Result :=
    'Setup could not install ' + Name + ' (installer exit code ' + IntToStr(ExitCode) + ').' + #13#10#13#10 +
    'MailDaan cannot run without it. Please make sure Windows is up to date ' +
    'and that no other setup is running, then run this setup again. ' +
    'No network connection is required.';
end;

// ---------------------------------------------------------------------------
// PrepareToInstall: run the embedded offline .NET Framework installer — from
// the setup's temporary extraction directory — only when it is missing.
// Everything happens before any MailDaan file is installed, so a failed
// prerequisite never leaves a half-installed application behind.
// ---------------------------------------------------------------------------

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
  PrereqPath: string;
  ProgressPage: TOutputProgressWizardPage;
begin
  Result := '';
  RestartNeeded := False;

  ProgressPage := CreateOutputProgressPage('MailDaan', 'Checking runtime prerequisites');
  ProgressPage.SetText('Checking runtime prerequisites...', '');
  ProgressPage.SetProgress(0, 1);
  ProgressPage.Show;
  try
    // ---- Microsoft .NET Framework 4.6.2+ ----------------------------------
    if not DotNetIsSupported then
    begin
      if not DotNetInstallerBundled then
      begin
        Result :=
          'MailDaan requires the Microsoft .NET Framework 4.6.2 runtime, which is ' +
          'not present on this computer.' + #13#10#13#10 +
          'The .NET Framework 4.6.2 or later is included with every supported version ' +
          'of Windows 10 and Windows 11, so this usually means Windows itself needs ' +
          'to be updated.' + #13#10#13#10 +
          'Please run Windows Update, then run this setup again.';
        Exit;
      end;
      ExtractTemporaryFile(DotNetInstallerName);
      PrereqPath := ExpandConstant('{tmp}\') + DotNetInstallerName;
      if not PrerequisiteIsIntact(PrereqPath, DotNetExpectedSha256) then
      begin
        Result := 'The embedded Microsoft .NET Framework installer is corrupted or has been tampered with. Setup cannot continue.';
        Exit;
      end;
      ProgressPage.SetText('Installing the Microsoft .NET Framework runtime...', 'This may take a few minutes');
      ProgressPage.SetProgress(0, 1);
      if (not RunPrerequisiteInstaller(PrereqPath, '/quiet /norestart', ExitCode)) or
         not ((ExitCode = 0) or (ExitCode = 3010)) then
      begin
        Result := InstallerResultMessage('the Microsoft .NET Framework runtime', ExitCode);
        Exit;
      end;
      if ExitCode = 3010 then
        RestartNeeded := True;
      if not DotNetIsSupported then
      begin
        Result := 'The Microsoft .NET Framework runtime was installed but is still not available. Please restart Windows and run this setup again.';
        Exit;
      end;
    end;
  finally
    ProgressPage.Hide;
  end;
end;

// ---------------------------------------------------------------------------
// CurStepChanged: after the files are installed, apply the directory
// permissions Microsoft requires for the Fixed Version WebView2 Runtime on
// Windows 10 (runtime 120 and newer): the renderer process runs inside an
// App Container, so 'ALL APPLICATION PACKAGES' and 'ALL RESTRICTED
// APPLICATION PACKAGES' must be able to read and execute the runtime files.
// ---------------------------------------------------------------------------

procedure CurStepChanged(CurStep: TSetupStep);
var
  RuntimePath, Sid: string;
  ResultCode: Integer;
begin
  if CurStep <> ssPostInstall then
    Exit;

  RuntimePath := ExpandConstant('{app}\_internal\webview2_runtime\') + WebView2RuntimeDirName;
  if not DirExists(RuntimePath) then
    Exit;

  Sid := '*S-1-15-2-2'; // ALL APPLICATION PACKAGES
  if (not Exec(ExpandConstant('{sys}\icacls.exe'), '"' + RuntimePath + '" /grant ' + Sid + ':(OI)(CI)(RX)', '',
               SW_HIDE, ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
    Log('MailDaan setup: icacls failed for ' + Sid + ' on the bundled WebView2 runtime (exit code ' + IntToStr(ResultCode) + ')');

  Sid := '*S-1-15-2-1'; // ALL RESTRICTED APPLICATION PACKAGES
  if (not Exec(ExpandConstant('{sys}\icacls.exe'), '"' + RuntimePath + '" /grant ' + Sid + ':(OI)(CI)(RX)', '',
               SW_HIDE, ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
    Log('MailDaan setup: icacls failed for ' + Sid + ' on the bundled WebView2 runtime (exit code ' + IntToStr(ResultCode) + ')');
end;

function NeedRestart: Boolean;
begin
  // A prerequisite installer asked for a reboot (exit code 3010): prompt the
  // user at the end of a successful installation instead of aborting it.
  Result := RestartNeeded;
end;

function BoolStr(Value: Boolean): string;
begin
  if Value then
    Result := 'yes'
  else
    Result := 'no';
end;

function InitializeSetup: Boolean;
begin
  // Single sanity log line for support purposes; the real prerequisite work
  // happens in PrepareToInstall above. The WebView2 Fixed Version Runtime is
  // bundled with the application and needs no system-level installation.
  Log('MailDaan setup: .NET Framework 4.6.2+ present=' + BoolStr(DotNetIsSupported) +
      ', bundled WebView2 runtime=' + WebView2RuntimeDirName);
  Result := True;
end;

