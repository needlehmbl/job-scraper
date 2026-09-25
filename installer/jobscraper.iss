#define AppVersion GetEnv("APP_VERSION")

[Setup]
AppId={{A3F1C9E2-7B4D-4E8A-9C2F-5D6B7A8E9F01}
AppName=JobScraper
AppVersion={#AppVersion}
DefaultDirName={autopf}\JobScraper
UsePreviousAppDir=yes
DirExistsWarning=auto
DefaultGroupName=JobScraper
OutputBaseFilename=JobScraper-Setup-{#AppVersion}
Compression=lzma2
PrivilegesRequired=lowest

[Files]
Source: "..\dist\JobScraper\*"; DestDir: "{app}"; Flags: recursesubdirs

[Icons]
Name: "{group}\JobScraper"; Filename: "{app}\JobScraper.exe"
Name: "{commondesktop}\JobScraper"; Filename: "{app}\JobScraper.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: checkedonce

[Run]
; First launch auto-opens the setup wizard (launcher.py ensure_config runs
; setup_wizard.py when .env is missing), then the app in the default browser.
Filename: "{app}\JobScraper.exe"; Description: "Open JobScraper now"; Flags: postinstall skipifsilent
