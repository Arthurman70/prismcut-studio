; Inno Setup script - builds a real Windows installer (Setup.exe) around the
; PyInstaller output. Run packaging\build_windows.bat first (or the PyInstaller
; step of it) so dist\PrismCut\PrismCut.exe exists, then:
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
; CI can override the version without editing this file:
;     ISCC.exe /DMyAppVersion=1.2.3 packaging\installer.iss
#ifndef MyAppVersion
  #define MyAppVersion "1.3.0"
#endif
#define MyAppName "PrismCut Studio"
#define MyAppPublisher "PrismCut Studio contributors"
#define MyAppURL "https://github.com/Arthurman70/prismcut-studio"
#define MyAppExeName "PrismCut.exe"

[Setup]
; Keep this GUID stable across releases - it's how Windows recognizes
; upgrades vs. fresh installs (regenerating it would orphan old installs).
AppId={{BC2968F4-DE79-4830-898D-0813B11BA474}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
; Per-user install, no admin/UAC prompt required (same pattern as VS Code's
; "User Installer" and most modern desktop-app installers).
PrivilegesRequired=lowest
; The auto-updater launches this installer while PrismCut.exe is still
; running (it can't replace its own locked .exe/DLLs otherwise - see
; core.updater.perform_self_update) - CloseApplications uses Windows
; Restart Manager to detect processes holding a lock on files being
; installed and close them gracefully, RestartApplications relaunches
; them afterward. Works silently under /VERYSILENT, and as a safety net
; also covers a user just double-clicking the installer by hand while the
; app happens to be open.
CloseApplications=yes
RestartApplications=yes
DefaultDirName={localappdata}\Programs\PrismCut Studio
DefaultGroupName=PrismCut Studio
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist\installer
OutputBaseFilename=PrismCut-Studio-Setup-{#MyAppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Fails loudly at compile time instead of shipping an installer with no payload.
#if !FileExists("..\dist\PrismCut\" + MyAppExeName)
  #error "dist\PrismCut\PrismCut.exe not found - run the PyInstaller build first (packaging\build_windows.bat)"
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\PrismCut\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; No skipifsilent: the auto-updater relies on THIS entry to relaunch the
; app after a /VERYSILENT install (it can't do so itself - see
; core.updater.launch_installer_detached's docstring for why), so it must
; still fire under silent installs, not just interactive ones.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall
