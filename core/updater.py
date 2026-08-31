"""
自動アップデート機能を提供するモジュール。
Cloudflare WorkerのAPIから最新バージョンを取得し、ZIPをダウンロード・展開して自身を上書き更新する。
"""
import os
import sys
import json
import urllib.request
import zipfile
import subprocess
import tempfile
import jwt
import datetime
import ssl
from PyQt6.QtCore import QThread, pyqtSignal
from core.version import APP_VERSION

class UpdateDownloaderThread(QThread):
    """アップデートのダウンロードと適用をバックグラウンドで行うスレッド"""
    finished = pyqtSignal(bool, str)

    def __init__(self, download_url):
        super().__init__()
        self.download_url = download_url

    def run(self):
        try:
            download_and_apply_update(self.download_url)
            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, str(e))

def _get_auth_headers():
    """auth.keyを読み込んでJWTを生成し、ヘッダーを返す"""
    headers = {"User-Agent": "ValoReco/1.0"}
    
    # Nuitkaビルド環境と開発環境の両方で正しくパスを解決する
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    key_path = os.path.join(base_path, "auth.key")
    
    if os.path.exists(key_path):
        try:
            with open(key_path, "rb") as f:
                private_key = f.read()
            payload = {
                "iat": datetime.datetime.utcnow(),
                "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=5)
            }
            token = jwt.encode(payload, private_key, algorithm="RS256")
            headers["Authorization"] = f"Bearer {token}"
        except Exception as e:
            print(f"Failed to generate JWT for updater: {e}")
    return headers

class UpdateCheckerThread(QThread):
    """バックグラウンドでアップデートを確認するスレッド"""
    update_available = pyqtSignal(str, str) # version, download_url
    error_occurred = pyqtSignal(str)

    def __init__(self, api_url):
        super().__init__()
        self.api_url = api_url

    def run(self):
        if not self.api_url:
            print("[Updater] Update check skipped: API URL is not set.")
            return
        try:
            req = urllib.request.Request(self.api_url, headers=_get_auth_headers())
            
            # Nuitka環境でのSSL証明書エラーを回避
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            
            with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
                if response.status != 200:
                    self.error_occurred.emit(f"HTTP Error: {response.status}")
                    return
                
                response_text = response.read().decode('utf-8')
                data = json.loads(response_text)
                latest_version = data.get("version")
                download_url = data.get("download_url")
                
                print(f"[Updater] Successfully checked for updates. Current: {APP_VERSION}, Latest: {latest_version}")
                
                if latest_version:
                    try:
                        # "1.0.1" のような文字列を (1, 0, 1) のタプルに変換して大小比較
                        latest_tuple = tuple(map(int, latest_version.lstrip('v').split('.')))
                        current_tuple = tuple(map(int, APP_VERSION.lstrip('v').split('.')))
                        if latest_tuple > current_tuple:
                            self.update_available.emit(latest_version, download_url)
                    except ValueError:
                        # バージョン文字列のパースに失敗した場合は単純な文字列比較にフォールバック
                        if latest_version != APP_VERSION:
                            self.update_available.emit(latest_version, download_url)
        except urllib.error.URLError as e:
            self.error_occurred.emit(f"Network error during update check: {e.reason}")
        except json.JSONDecodeError as e:
            self.error_occurred.emit(f"Invalid JSON response during update check: {e}")
        except Exception as e:
            self.error_occurred.emit(f"Unexpected error during update check: {e}")

class NoAuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    """リダイレクト先が別ドメインの場合、Authorizationヘッダーを削除するハンドラ"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newreq = super().redirect_request(req, fp, code, msg, headers, newurl)
        if req.host != newreq.host:
            if 'Authorization' in newreq.headers:
                del newreq.headers['Authorization']
            if 'Authorization' in newreq.unredirected_hdrs:
                del newreq.unredirected_hdrs['Authorization']
        return newreq

def download_and_apply_update(download_url: str):
    """ZIPをダウンロードして展開し、バッチファイル経由で自身を上書きして再起動する"""
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "update.zip")
    
    # 1. ZIPのダウンロード
    req = urllib.request.Request(download_url, headers=_get_auth_headers())
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        NoAuthRedirectHandler()
    )
    
    try:
        with opener.open(req, timeout=30) as response, open(zip_path, 'wb') as out_file:
            if response.status != 200:
                raise RuntimeError(f"Failed to download update. HTTP Status: {response.status}")
            out_file.write(response.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='ignore')
        raise RuntimeError(f"HTTP Error {e.code}: {e.reason}\nDetails: {error_body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error while downloading update: {e.reason}")
        
    # 2. ZIPの解凍
    extract_dir = os.path.join(temp_dir, "extracted")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)
        
    # 3. 解凍先から新しい実行ファイル(.exe)を探す
    new_exe_path = None
    for root, dirs, files in os.walk(extract_dir):
        for file in files:
            if file.endswith(".exe"):
                new_exe_path = os.path.join(root, file)
                break
        if new_exe_path:
            break
            
    if not new_exe_path:
        raise FileNotFoundError("Executable (.exe) not found in the downloaded update archive.")

    # 実行ファイルのパスを特定する
    # PyInstallerの場合は sys.frozen が True になり、sys.executable が自身のexeを指す
    if getattr(sys, 'frozen', False):
        current_exe_path = sys.executable
    else:
        # Nuitka等で sys.frozen がない場合、sys.executable が元の python.exe を指すことがあるため sys.argv[0] を使用する
        current_exe_path = os.path.abspath(sys.argv[0])

    # 開発環境(Pythonスクリプト実行)の場合は更新をスキップ
    if not current_exe_path.lower().endswith(".exe") or "python" in os.path.basename(current_exe_path).lower():
        raise RuntimeError(f"Cannot apply update in development environment (running via {os.path.basename(current_exe_path)}).")
    
    # 4. 上書き更新用バッチファイルの作成
    bat_path = os.path.join(temp_dir, "update.bat")
    with open(bat_path, "w", encoding="utf-8") as bat_file:
        bat_file.write("@echo off\n")
        # コマンドプロンプトをUTF-8モードに変更し、日本語パスの文字化けを防ぐ
        bat_file.write("chcp 65001 > nul\n")
        bat_file.write(":retry\n")
        # アプリが完全に終了してファイルのロックが解除されるのを待つ
        bat_file.write("timeout /t 1 /nobreak > nul\n")
        # 新しいexeで古いexeを上書き
        bat_file.write(f'move /y "{new_exe_path}" "{current_exe_path}"\n')
        # 上書きに失敗した場合（まだアプリが起動中でファイルがロックされている場合など）はリトライ
        bat_file.write("if errorlevel 1 goto retry\n")
        # 新しいexeを起動
        bat_file.write(f'start "" "{current_exe_path}"\n')
        # バッチファイル自身を削除
        bat_file.write('del "%~f0"\n')
        
    # 5. バッチファイルを非表示で実行してアプリを終了
    # shell=True に依存せず、明示的に cmd.exe を呼び出す
    subprocess.Popen(["cmd.exe", "/c", bat_path], creationflags=subprocess.CREATE_NO_WINDOW)
    
    # バッチファイル起動後、即座にプロセスを強制終了してファイルのロックを解除する
    # QCoreApplication.quit() では終了が遅延するため os._exit(0) を使用
    os._exit(0)
