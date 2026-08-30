; Inno Setup Script for Valorant Recorder
; このスクリプトはNuitkaでビルドされた main.exe をインストーラ化します。

[Setup]
AppName=ValoReco ヴァロレコ
AppVersion=1.0.0
AppPublisher=Your Name
AppPublisherURL=https://your-website.com/
; 自動アップデート時の権限エラーを防ぐため、LocalAppDataにインストールします
DefaultDirName={localappdata}\ValoReco
DefaultGroupName=ValoReco ヴァロレコ
; 出力されるインストーラのファイル名
OutputBaseFilename=ValoReco_Setup
; 圧縮設定
Compression=lzma2/ultra64
SolidCompression=yes
; 管理者権限を要求しない（ユーザー権限でインストール可能にするため）
PrivilegesRequired=lowest
; アンインストーラの設定
UninstallDisplayIcon={app}\ValoReco.exe
; インストーラ自体のアイコン
SetupIconFile=assets\icon.ico

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Nuitkaでビルドされた単一ファイル(ValoReco.exe)を指定します
Source: "ValoReco.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; スタートメニューとデスクトップのショートカット作成
Name: "{group}\ValoReco ヴァロレコ"; Filename: "{app}\ValoReco.exe"
Name: "{group}\{cm:UninstallProgram,ValoReco ヴァロレコ}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\ValoReco ヴァロレコ"; Filename: "{app}\ValoReco.exe"; Tasks: desktopicon

[Run]
; インストール完了後にアプリを起動するオプション
Filename: "{app}\ValoReco.exe"; Description: "{cm:LaunchProgram,ValoReco ヴァロレコ}"; Flags: nowait postinstall skipifsilent

[Code]
// インストール開始前にバックグラウンドで動いている ValoReco.exe を強制終了する
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
  begin
    Exec('taskkill.exe', '/F /IM ValoReco.exe /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;
