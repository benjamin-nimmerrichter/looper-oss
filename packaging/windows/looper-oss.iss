; Inno Setup script for the Looper OSS Windows installer.
;
;   iscc /DAppVersion=0.1.0 packaging\windows\looper-oss.iss
;
; Per-user install (%LOCALAPPDATA%), no admin prompt: on a pedal or a shared
; laptop the user should not need an administrator to install a loop station.
; The app keeps sessions/ and logs/ next to itself in %LOCALAPPDATA%\looper-oss,
; which is writable without elevation.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Looper OSS"
#define AppExe "LooperOSS.exe"

[Setup]
AppId={{8F2C1A54-9E7B-4C31-B0D7-1F5E6C2A9D31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Looper OSS contributors
; Never %LOCALAPPDATA%\\looper-oss\\app: that is where the app stages a
; per-version copy AND keeps sessions/, so an uninstall could remove the
; user's music. Programs live under Programs, state lives under app/.
; No space in the path: SuperCollider assembles the server command as a
; plain string and Windows splits it at the space, so 'Programs\Looper OSS'
; became 'Programs\Looper' + ' OSS' and scsynth never started.
DefaultDirName={localappdata}\Programs\LooperOSS
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=looper-oss-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=looper-oss.ico
UninstallDisplayIcon={app}\{#AppExe}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; SuperCollider lives in Program Files; we only point at it, never modify it
LicenseFile=..\..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\..\dist\LooperOSS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; IconFilename explicitly: the shortcut then shows the app icon even if Windows
; has a cached copy of an older LooperOSS.exe under the same name.
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; logs and generated config are disposable; sessions/ is deliberately NOT listed
Type: filesandordirs; Name: "{app}\logs"
