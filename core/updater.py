"""
自動アップデート機能を提供するモジュール。
Cloudflare WorkerのAPIから最新バージョンを取得し、ZIPをダウンロード・展開して自身を上書き更新する。
"""
import datetime
import json
import logging
import os
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

import jwt
from PyQt6.QtCore import QThread, pyqtSignal

from core.config import get_app_base_dir
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

def _resolve_auth_key_path() -> str:
    """実行形態に応じて auth.key の実体パスを探索して返す"""
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, "auth.key"))
    candidates.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auth.key"))
    candidates.append(os.path.join(get_app_base_dir(), "auth.key"))

    for path in candidates:
        if path and os.path.exists(path):
            return path
    raise FileNotFoundError(f"Authentication key (auth.key) not found in: {candidates}")


def _get_auth_headers():
    """auth.keyを読み込んでJWTを生成し、ヘッダーを返す"""
    headers = {"User-Agent": "ValoReco/1.0"}
    key_path = _resolve_auth_key_path()
    with open(key_path, "rb") as f:
        private_key = f.read()

    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(minutes=5)).timestamp()),
    }
    token = jwt.encode(payload, private_key, algorithm="RS256")
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_version_tuple(version_str: str) -> tuple[int, ...]:
    """バージョン文字列を比較可能な整数タプルへ正規化する"""
    cleaned = str(version_str or "").strip().lstrip("vV")
    match = re.match(r"^(\d+(?:\.\d+)*)", cleaned)
    if not match:
        raise ValueError(f"Invalid version format: {version_str!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def _is_newer_version(latest_version: str, current_version: str) -> bool:
    """latest_version が current_version より新しいかを判定する"""
    try:
        latest_parts = _parse_version_tuple(latest_version)
        current_parts = _parse_version_tuple(current_version)
        max_len = max(len(latest_parts), len(current_parts))
        latest_padded = latest_parts + (0,) * (max_len - len(latest_parts))
        current_padded = current_parts + (0,) * (max_len - len(current_parts))
        return latest_padded > current_padded
    except ValueError:
        return str(latest_version or "").strip() != str(current_version or "").strip()

class UpdateCheckerThread(QThread):
    """バックグラウンドでアップデートを確認するスレッド"""
    update_available = pyqtSignal(str, str) # version, download_url
    error_occurred = pyqtSignal(str)

    def __init__(self, api_url):
        super().__init__()
        self.api_url = api_url

    def run(self):
        if not self.api_url:
            logging.warning("[Updater] Update check skipped: API URL is not set.")
            return
        try:
            req = urllib.request.Request(self.api_url, headers=_get_auth_headers())

            # Nuitka環境でのSSL証明書検証失敗を回避
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
                if response.status != 200:
                    msg = f"HTTP Error: {response.status}"
                    logging.warning(f"[Updater] {msg}")
                    self.error_occurred.emit(msg)
                    return

                response_text = response.read().decode("utf-8")
                data = json.loads(response_text)
                latest_version = str(data.get("version") or "").strip()
                download_url = str(data.get("download_url") or "").strip()

                logging.info(
                    f"[Updater] Update check succeeded. Current={APP_VERSION}, Latest={latest_version}"
                )

                if latest_version and _is_newer_version(latest_version, APP_VERSION):
                    if not download_url:
                        msg = "Update found, but download_url is missing."
                        logging.warning(f"[Updater] {msg}")
                        self.error_occurred.emit(msg)
                        return
                    self.update_available.emit(latest_version, download_url)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="ignore") if e.fp else ""
            msg = f"HTTP Error {e.code} during update check: {e.reason} {error_body}".strip()
            logging.warning(f"[Updater] {msg}")
            self.error_occurred.emit(msg)
        except urllib.error.URLError as e:
            msg = f"Network error during update check: {e.reason}"
            logging.warning(f"[Updater] {msg}")
            self.error_occurred.emit(msg)
        except json.JSONDecodeError as e:
            msg = f"Invalid JSON response during update check: {e}"
            logging.warning(f"[Updater] {msg}")
            self.error_occurred.emit(msg)
        except Exception as e:
            msg = f"Unexpected error during update check: {e}"
            logging.warning(f"[Updater] {msg}")
            self.error_occurred.emit(msg)

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

    # 一時展開フォルダではなく実際の実行ファイル(.exe)のパスを特定する
    from core.config import get_real_executable_path
    current_exe_path = get_real_executable_path()

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
