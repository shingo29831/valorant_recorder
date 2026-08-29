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
from PyQt6.QtCore import QThread, pyqtSignal
from core.version import APP_VERSION

class UpdateCheckerThread(QThread):
    """バックグラウンドでアップデートを確認するスレッド"""
    update_available = pyqtSignal(str, str) # version, download_url

    def __init__(self, api_url):
        super().__init__()
        self.api_url = api_url

    def run(self):
        try:
            req = urllib.request.Request(self.api_url, headers={"User-Agent": "ValorantRecorder/1.0"})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                latest_version = data.get("version")
                download_url = data.get("download_url")
                
                if latest_version and latest_version != APP_VERSION:
                    self.update_available.emit(latest_version, download_url)
        except Exception as e:
            print(f"Update check failed: {e}")

def download_and_apply_update(download_url: str):
    """ZIPをダウンロードして展開し、バッチファイル経由で自身を上書きして再起動する"""
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "update.zip")
    
    # 1. ZIPのダウンロード
    req = urllib.request.Request(download_url, headers={"User-Agent": "ValorantRecorder/1.0"})
    with urllib.request.urlopen(req) as response, open(zip_path, 'wb') as out_file:
        out_file.write(response.read())
        
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
    sys.exit(0)
