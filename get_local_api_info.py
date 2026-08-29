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

def get_client_region():
    info = get_lockfile_info()
    if not info:
        return None
    
    url = f"{info['protocol']}://127.0.0.1:{info['port']}/riotclient/region-locale"
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
            region = data.get('region')
            if region:
                return region.lower()
    except Exception:
        pass
    return None

if __name__ == "__main__":
    info = get_lockfile_info()
    if info:
        print("Lockfile Info:", info)
        print("Access Token:", get_riot_access_token())
        name, tag = get_current_player()
        region = get_client_region()
        print(f"Player: {name}#{tag} (Region: {region})")
    else:
        print("Failed to get lockfile info.")
