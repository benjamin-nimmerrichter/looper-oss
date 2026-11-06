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
DefaultDirName={localappdata}\looper-oss\app
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
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; logs and generated config are disposable; sessions/ is deliberately NOT listed
Type: filesandordirs; Name: "{app}\logs"
