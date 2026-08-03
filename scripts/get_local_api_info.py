import os

def get_lockfile_info():
    # Riot Clientのlockfileパスを構築
    local_app_data = os.environ.get('LOCALAPPDATA')
    if not local_app_data:
        print("エラー: LOCALAPPDATA 環境変数が見つかりません。")
        return

    lockfile_path = os.path.join(local_app_data, 'Riot Games', 'Riot Client', 'Config', 'lockfile')
    
    if not os.path.exists(lockfile_path):
        print(f"エラー: lockfileが見つかりません。\nパス: {lockfile_path}")
        print("Valorant（Riot Client）を起動してから再度実行してください。")
        return

    try:
        with open(lockfile_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            
        # lockfileのフォーマット: name:PID:port:password:protocol
        parts = content.split(':')
        if len(parts) >= 5:
            process_name = parts[0]
            pid = parts[1]
            port = parts[2]
            password = parts[3]
            protocol = parts[4]
            
            print("=== Local Client API 接続情報 ===")
            print(f"プロセス名 : {process_name}")
            print(f"PID        : {pid}")
            print(f"ポート番号 : {port}")
            print(f"パスワード : {password}")
            print(f"プロトコル : {protocol}")
            print("=================================")
            print("※ この情報を使って、ローカルでキルイベント等を取得します。")
        else:
            print("エラー: lockfileのフォーマットが予期せぬ形式です。")
            print(f"内容: {content}")
            
    except PermissionError:
        print("エラー: lockfileへのアクセス権限がありません。")

if __name__ == "__main__":
    get_lockfile_info()