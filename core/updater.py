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
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    key_path = os.path.join(project_root, "auth.key")
    
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
            self.error_occurred.emit("Update check skipped: API URL is not set.")
            return
        try:
            req = urllib.request.Request(self.api_url, headers=_get_auth_headers())
            with urllib.request.urlopen(req, timeout=10) as response:
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

def download_and_apply_update(download_url: str):
    """ZIPをダウンロードして展開し、バッチファイル経由で自身を上書きして再起動する"""
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "update.zip")
    
    # 1. ZIPのダウンロード
    req = urllib.request.Request(download_url, headers=_get_auth_headers())
    try:
        with urllib.request.urlopen(req, timeout=30) as response, open(zip_path, 'wb') as out_file:
            if response.status != 200:
                raise RuntimeError(f"Failed to download update. HTTP Status: {response.status}")
            out_file.write(response.read())
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

    # 開発環境(Pythonスクリプト実行)の場合は更新をスキップ
    current_exe_path = sys.executable
    if not current_exe_path.endswith(".exe") or "python" in os.path.basename(current_exe_path).lower():
        raise RuntimeError("Cannot apply update in development environment (running via python.exe).")
    
    # 4. 上書き更新用バッチファイルの作成
    bat_path = os.path.join(temp_dir, "update.bat")
    with open(bat_path, "w", encoding="utf-8") as bat_file:
        bat_file.write("@echo off\n")
        # アプリが完全に終了するのを待つ
        bat_file.write("timeout /t 2 /nobreak > nul\n")
        # 新しいexeで古いexeを上書き
        bat_file.write(f'move /y "{new_exe_path}" "{current_exe_path}"\n')
        # 新しいexeを起動
        bat_file.write(f'start "" "{current_exe_path}"\n')
        # バッチファイル自身を削除
        bat_file.write('del "%~f0"\n')
        
    # 5. バッチファイルを非表示で実行してアプリを終了
    subprocess.Popen([bat_path], shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
    # スレッド内での sys.exit() は SystemExit 例外を投げるだけなので、ここでは終了させず呼び出し元に委ねる
