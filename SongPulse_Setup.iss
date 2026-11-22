#define MyAppName "SongPulse"
#define MyAppVersion "1.1.1"
#define MyAppPublisher "Greatly07"
#define MyAppURL "https://github.com/Greatly07/songpulse"
#define MyAppExeName "SongPulse.exe"
#define AiPackZipName "SongPulse_AI_Pack_v1.1.1.zip"
#define AiPackURL "https://github.com/Greatly07/songpulse/releases/download/v1.1.1/SongPulse_AI_Pack_v1.1.1.zip"
#define UvrBaseURL "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models"
#define VCRedistURL "https://aka.ms/vs/17/release/vc_redist.x64.exe"

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

[Types]
Name: "compact"; Description: "Compact installation (Core app only, saves space)"
Name: "custom"; Description: "Custom installation (Choose optional AI rescue engine and models)"; Flags: iscustom

[Components]
Name: "core"; Description: "SongPulse Core Application (Live & Buffer Recognition, Library, Themes)"; Types: compact custom; Flags: fixed
Name: "ai_engine"; Description: "Optional AI Rescue Engine (Downloads ~380 MB pack + FFmpeg)"; Types: custom
Name: "ai_models"; Description: "Optional Tricky-Track AI Models (Unchecked by default; also downloadable in-app)"; Types: custom
Name: "ai_models\dereverb_hq"; Description: "De-Reverb HQ by FoxJoy (Recommended, ~66 MB)"; Types: custom
Name: "ai_models\deecho"; Description: "UVR De-Echo & De-Reverb (~35 MB)"; Types: custom
Name: "ai_models\denoise"; Description: "UVR DeNoise for SFX & Static (~35 MB)"; Types: custom
Name: "ai_models\crowd"; Description: "UVR Crowd & Chatter Remover (~66 MB)"; Types: custom
Name: "ai_models\inst_hq3"; Description: "UVR Inst HQ 3 Speech Stripper (~66 MB)"; Types: custom

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "startup"; Description: "Run SongPulse automatically at Windows startup (hidden in system tray)"; GroupDescription: "Startup:"

[Files]
Source: "dist\SongPulse_Core\*"; DestDir: "{app}"; Components: core; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"" --tray"; Flags: uninsdeletevalue; Tasks: startup
Root: HKCU; Subkey: "Software\Classes\songpulse"; ValueType: string; ValueName: ""; ValueData: "URL:SongPulse Protocol"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\songpulse"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\songpulse\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall

[Code]
var
  DownloadPage: TDownloadWizardPage;
  NeedVCRedist: Boolean;
  HasLocalAiPack: Boolean;
  LocalAiPackPath: String;

function IsVCRedistInstalled(): Boolean;
var
  Installed: Cardinal;
begin
  Result :=
    RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) and
    (Installed = 1);
  if not Result then
    Result :=
      RegQueryDWordValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) and
      (Installed = 1);
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard;
begin
  NeedVCRedist := not IsVCRedistInstalled();
  LocalAiPackPath := ExpandConstant('{src}\{#AiPackZipName}');
  HasLocalAiPack := FileExists(LocalAiPackPath);
  DownloadPage := CreateDownloadPage(
    SetupMessage(msgWizardPreparing),
    'Downloading selected optional components and prerequisites...',
    @OnDownloadProgress
  );
end;

function ShouldDownloadAny(): Boolean;
begin
  Result :=
    NeedVCRedist or
    (WizardIsComponentSelected('ai_engine') and (not HasLocalAiPack)) or
    WizardIsComponentSelected('ai_models\dereverb_hq') or
    WizardIsComponentSelected('ai_models\deecho') or
    WizardIsComponentSelected('ai_models\denoise') or
    WizardIsComponentSelected('ai_models\crowd') or
    WizardIsComponentSelected('ai_models\inst_hq3');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpReady) and ShouldDownloadAny() then
  begin
    DownloadPage.Clear;
    if NeedVCRedist then
      DownloadPage.Add('{#VCRedistURL}', 'vc_redist.x64.exe', '');
    if WizardIsComponentSelected('ai_engine') and (not HasLocalAiPack) then
      DownloadPage.Add('{#AiPackURL}', '{#AiPackZipName}', '');
    if WizardIsComponentSelected('ai_models\dereverb_hq') then
      DownloadPage.Add('{#UvrBaseURL}/Reverb_HQ_By_FoxJoy.onnx', 'Reverb_HQ_By_FoxJoy.onnx', '');
    if WizardIsComponentSelected('ai_models\deecho') then
      DownloadPage.Add('{#UvrBaseURL}/UVR-DeEcho-DeReverb.pth', 'UVR-DeEcho-DeReverb.pth', '');
    if WizardIsComponentSelected('ai_models\denoise') then
      DownloadPage.Add('{#UvrBaseURL}/UVR-DeNoise.pth', 'UVR-DeNoise.pth', '');
    if WizardIsComponentSelected('ai_models\crowd') then
      DownloadPage.Add('{#UvrBaseURL}/UVR-MDX-NET_Crowd_HQ_1.onnx', 'UVR-MDX-NET_Crowd_HQ_1.onnx', '');
    if WizardIsComponentSelected('ai_models\inst_hq3') then
      DownloadPage.Add('{#UvrBaseURL}/UVR-MDX-NET-Inst_HQ_3.onnx', 'UVR-MDX-NET-Inst_HQ_3.onnx', '');

    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
        Result := True;
      except
        if DownloadPage.AbortedByUser then
        begin
          Log('Download aborted by user.');
          Result := False;
        end
        else
        begin
          SuppressibleMsgBox(
            'One or more optional downloads failed (' + GetExceptionMessage + ').' + #13#10 +
            'SongPulse Core will still install. You can download optional AI components in-app later.',
            mbInformation, MB_OK, IDOK
          );
          Result := True;
        end;
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

procedure CopyModelIfPresent(const FileName: String);
var
  SrcFile, DestDir: String;
begin
  SrcFile := ExpandConstant('{tmp}\') + FileName;
  DestDir := ExpandConstant('{localappdata}\{#MyAppName}\models');
  if FileExists(SrcFile) then
  begin
    ForceDirectories(DestDir);
    FileCopy(SrcFile, DestDir + '\' + FileName, False);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  ZipPath, DestDir, PsCmd, VcPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    VcPath := ExpandConstant('{tmp}\vc_redist.x64.exe');
    if NeedVCRedist and FileExists(VcPath) then
    begin
      Exec(VcPath, '/install /passive /norestart', '', SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode);
    end;

    if WizardIsComponentSelected('ai_engine') then
    begin
      if HasLocalAiPack then
        ZipPath := LocalAiPackPath
      else
        ZipPath := ExpandConstant('{tmp}\{#AiPackZipName}');

      if FileExists(ZipPath) then
      begin
        DestDir := ExpandConstant('{app}\_internal');
        ForceDirectories(DestDir);
        PsCmd := '-NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath ''' + ZipPath + ''' -DestinationPath ''' + DestDir + ''' -Force"';
        Exec('powershell.exe', PsCmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      end;
    end;

    CopyModelIfPresent('Reverb_HQ_By_FoxJoy.onnx');
    CopyModelIfPresent('UVR-DeEcho-DeReverb.pth');
    CopyModelIfPresent('UVR-DeNoise.pth');
    CopyModelIfPresent('UVR-MDX-NET_Crowd_HQ_1.onnx');
    CopyModelIfPresent('UVR-MDX-NET-Inst_HQ_3.onnx');
  end;
end;
