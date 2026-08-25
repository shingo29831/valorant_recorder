import os
import base64
import json
import urllib.request
import ssl

def get_lockfile_info():
    local_app_data = os.environ.get('LOCALAPPDATA')
    if not local_app_data:
        return None

    lockfile_path = os.path.join(local_app_data, 'Riot Games', 'Riot Client', 'Config', 'lockfile')
    
    if not os.path.exists(lockfile_path):
        return None

    try:
        with open(lockfile_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            
        parts = content.split(':')
        if len(parts) >= 5:
            return {
                'port': parts[2],
                'password': parts[3],
                'protocol': parts[4]
            }
    except Exception:
        pass
    return None

def get_riot_access_token():
    info = get_lockfile_info()
    if not info:
        return None
    
    url = f"{info['protocol']}://127.0.0.1:{info['port']}/entitlements/v1/token"
    auth = base64.b64encode(f"riot:{info['password']}".encode()).decode()
    
    req = urllib.request.Request(url, headers={
        'Authorization': f'Basic {auth}',
        'Accept': 'application/json'
    })
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            data = json.loads(response.read().decode())
            return data.get('accessToken')
    except Exception:
        return None

def get_current_player():
    info = get_lockfile_info()
    if not info:
        return None, None
    
    url = f"{info['protocol']}://127.0.0.1:{info['port']}/chat/v1/session"
    auth = base64.b64encode(f"riot:{info['password']}".encode()).decode()
    
    req = urllib.request.Request(url, headers={
        'Authorization': f'Basic {auth}',
        'Accept': 'application/json'
    })
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            data = json.loads(response.read().decode())
            return data.get('game_name'), data.get('game_tag')
    except Exception:
        return None, None

if __name__ == "__main__":
    info = get_lockfile_info()
    if info:
        print("Lockfile Info:", info)
        print("Access Token:", get_riot_access_token())
        name, tag = get_current_player()
        print(f"Player: {name}#{tag}")
    else:
        print("Failed to get lockfile info.")