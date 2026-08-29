; Inno Setup Script for Valorant Recorder
; このスクリプトはNuitkaでビルドされた main.exe をインストーラ化します。

[Setup]
AppName=Valorant Recorder
AppVersion=1.0.0
AppPublisher=Your Name
AppPublisherURL=https://your-website.com/
; 自動アップデート時の権限エラーを防ぐため、LocalAppDataにインストールします
DefaultDirName={localappdata}\ValorantRecorder
DefaultGroupName=Valorant Recorder
; 出力されるインストーラのファイル名
OutputBaseFilename=ValorantRecorder_Setup
; 圧縮設定
Compression=lzma2/ultra64
SolidCompression=yes
; 管理者権限を要求しない（ユーザー権限でインストール可能にするため）
PrivilegesRequired=lowest
; アンインストーラの設定
UninstallDisplayIcon={app}\main.exe

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Nuitkaでビルドされた単一ファイル(main.exe)を指定します
Source: "main.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; スタートメニューとデスクトップのショートカット作成
Name: "{group}\Valorant Recorder"; Filename: "{app}\main.exe"
Name: "{group}\{cm:UninstallProgram,Valorant Recorder}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Valorant Recorder"; Filename: "{app}\main.exe"; Tasks: desktopicon

[Run]
; インストール完了後にアプリを起動するオプション
Filename: "{app}\main.exe"; Description: "{cm:LaunchProgram,Valorant Recorder}"; Flags: nowait postinstall skipifsilent
