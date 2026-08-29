import urllib.request
import urllib.parse
import urllib.error
import json
import time
import jwt
import datetime
import os

class HenrikAPI:
    def __init__(self, region: str, name: str, tag: str):
        # APIが受け付ける有効なシャード名に確実に変換 (jp -> apなど)
        region_map = {
            'jp': 'ap',
            'kr': 'kr',
            'na': 'na',
            'eu': 'eu',
            'latam': 'latam',
            'br': 'br',
            'ap': 'ap'
        }
        safe_region = str(region).lower()
        self.region = region_map.get(safe_region, 'ap')
        
        # 前後の空白を除去してからURLエンコード
        self.name = urllib.parse.quote(str(name).strip())
        self.tag = urllib.parse.quote(str(tag).strip())
        self.base_headers = {
            'User-Agent': 'ValorantRecorder/1.0'
        }
        
        # 秘密鍵の読み込み (プロジェクトルートの auth.key を絶対パスで参照)
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        key_path = os.path.join(project_root, "auth.key")
        
        if not os.path.exists(key_path):
            raise FileNotFoundError(f"Authentication key not found at {key_path}. Please run 'python scripts/generate_keys.py' first.")
            
        with open(key_path, "rb") as f:
            self.private_key = f.read()

    def _get_headers(self):
        headers = self.base_headers.copy()
        
        # JWTの生成 (有効期限1分)
        payload = {
            "iat": datetime.datetime.utcnow(),
            "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=1)
        }
        token = jwt.encode(payload, self.private_key, algorithm="RS256")
        headers['Authorization'] = f"Bearer {token}"
        
        return headers

    def fetch_latest_match(self, retries: int = 3, delay: int = 10) -> dict:
        url = f"https://valoreco-api.meld-task.com/valorant/v3/matches/{self.region}/{self.name}/{self.tag}?size=1"
        
        for attempt in range(retries):
            req = urllib.request.Request(url, headers=self._get_headers())
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    if data.get('status') == 200 and data.get('data'):
                        return data['data'][0]
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(delay)
                    continue
                error_body = e.read().decode('utf-8') if e.fp else ""
                raise RuntimeError(f"HTTP {e.code}: {e.reason} - {error_body}")
            except Exception as e:
                if attempt == retries - 1:
                    raise RuntimeError(f"API Request failed: {e}")
            time.sleep(delay)
        raise RuntimeError("Failed to fetch match data after retries.")

    def fetch_mmr_change(self, match_id: str, retries: int = 3, delay: int = 10) -> int:
        url = f"https://valoreco-api.meld-task.com/valorant/v1/mmr-history/{self.region}/{self.name}/{self.tag}"
        
        for attempt in range(retries):
            req = urllib.request.Request(url, headers=self._get_headers())
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    if data.get('status') == 200 and data.get('data'):
                        for history in data['data']:
                            if history.get('match_id') == match_id:
                                return history.get('mmr_change_to_last_game', 0)
                        return 0
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(delay)
                    continue
                error_body = e.read().decode('utf-8') if e.fp else ""
                raise RuntimeError(f"HTTP {e.code}: {e.reason} - {error_body}")
            except Exception as e:
                if attempt == retries - 1:
                    raise RuntimeError(f"API Request failed: {e}")
            time.sleep(delay)
        return 0