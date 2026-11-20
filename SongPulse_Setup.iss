#define MyAppName "SongPulse"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Greatly07"
#define MyAppURL "https://github.com/Greatly07/songpulse"
#define MyAppExeName "SongPulse.exe"

[Setup]
AppId={{8E2C6B54-19B3-4C5E-A2A4-9F1A4D7E2100}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
PrivilegesRequired=lowest
DefaultDirName={userpf}\{#MyAppName}
DefaultGroupName={#MyAppName}
SetupIconFile=songpulse.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=release
OutputBaseFilename=SongPulse_Setup_v{#MyAppVersion}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "startup"; Description: "Run SongPulse automatically at Windows startup (hidden in system tray)"; GroupDescription: "Startup:"

[Files]
Source: "dist\SongPulse\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"" --tray"; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent