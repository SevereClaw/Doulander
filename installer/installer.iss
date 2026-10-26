; Установщик Downloader (Inno Setup 6). Собирается автоматически на GitHub:
;   ISCC.exe /DMyAppVersion=1.8.0 installer\installer.iss
; Ставится без прав администратора в %LOCALAPPDATA%\Programs\Downloader —
; поэтому программа может сама обновляться и хранить настройки рядом с собой.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{8F3C2A1E-5B7D-4E9A-9C1B-D0A1E2F3B4C5}
AppName=Downloader
AppVersion={#MyAppVersion}
AppVerName=Downloader {#MyAppVersion}
AppPublisher=SevereClaw
VersionInfoVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\Downloader
DefaultGroupName=Downloader
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\dist-setup
OutputBaseFilename=DownloaderSetup
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\Downloader.exe
UninstallDisplayName=Downloader
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\Downloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Downloader"; Filename: "{app}\Downloader.exe"
Name: "{autoprograms}\Обновить Downloader"; Filename: "{app}\update.bat"; IconFilename: "{app}\Downloader.exe"
Name: "{autodesktop}\Downloader"; Filename: "{app}\Downloader.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Downloader.exe"; Description: "{cm:LaunchProgram,Downloader}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; убрать из автозагрузки Windows, если автозапуск включали в настройках
Filename: "{cmd}"; Parameters: "/c reg delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v Downloader /f"; Flags: runhidden; RunOnceId: "RemoveAutostart"

[UninstallDelete]
; файлы, которые программа создаёт сама (скачанные видео не трогаем — они в «Загрузках»)
Type: files; Name: "{app}\settings.json"
Type: files; Name: "{app}\history.json"
Type: files; Name: "{app}\queue.json"
Type: files; Name: "{app}\update_info.txt"
; папки, которые программа обновляет сама (там могут появиться новые файлы)
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\yt-dlp"
Type: filesandordirs; Name: "{app}\yt-dlp_new"
Type: filesandordirs; Name: "{app}\yt-dlp_old"
Type: files; Name: "{app}\*.part"
Type: dirifempty; Name: "{app}"
