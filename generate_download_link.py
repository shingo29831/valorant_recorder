import jwt
import datetime
import os

def generate_link():
    # 秘密鍵の読み込み
    key_path = "auth.key"
    if not os.path.exists(key_path):
        print("Error: auth.key not found.")
        return

    with open(key_path, "rb") as f:
        private_key = f.read()

    # 有効期限を長めに設定 (例: 24時間)
    # ※ インストーラーをダウンロードする人に渡すため、少し長めにします
    payload = {
        "iat": datetime.datetime.utcnow(),
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=24)
    }

    # JWTの生成
    token = jwt.encode(payload, private_key, algorithm="RS256")
    
    # ダウンロードURLの生成
    base_url = "https://valoreco-api.meld-task.com/download/installer"
    download_url = f"{base_url}?token={token}"
    
    print("=== インストーラー ダウンロードURL ===")
    print("以下のURLをブラウザに貼り付けてください（有効期限: 24時間）\n")
    print(download_url)
    print("\n======================================")

if __name__ == "__main__":
    generate_link()
