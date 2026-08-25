import sys
import os
import json

# プロジェクトルートをPythonのパスに追加
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.henrik_api import HenrikAPI

def main():
    print("=== API通信テスト開始 ===")
    
    # テスト用のRiot ID (必要に応じてご自身のIDに変更してください)
    region = "ap"
    name = "shingo"
    tag = "7445"
    
    print(f"対象: {name}#{tag} (Region: {region})")
    
    # auth.keyの存在確認
    if not os.path.exists("auth.key"):
        print("\n❌ エラー: 'auth.key' が見つかりません。")
        print("先に 'python scripts/generate_keys.py' を実行して鍵を生成してください。")
        return

    try:
        api = HenrikAPI(region, name, tag)
        print(f"リクエスト先: https://valo-reco-api.meld-task.com/valorant/v3/matches/{region}/{name}/{tag}?size=1")
        print("最新の試合データを取得中...")
        
        match_data = api.fetch_latest_match(retries=1)
        
        if match_data:
            print("\n✅ 通信成功！プロキシサーバーを経由してデータを取得しました。")
            print("--- 取得データの一部 ---")
            print(f"試合ID: {match_data.get('metadata', {}).get('matchid')}")
            print(f"マップ: {match_data.get('metadata', {}).get('map')}")
            print(f"モード: {match_data.get('metadata', {}).get('mode')}")
            print("------------------------")
        else:
            print("\n⚠️ 通信は成功しましたが、データが空です。")
            
    except Exception as e:
        print(f"\n❌ 通信エラー発生: {e}")
        print("\n【トラブルシューティング】")
        print("1. 'auth.pub' の中身が Cloudflare Worker の環境変数 'PUBLIC_KEY' に正しく設定されているか確認してください。")
        print("2. Cloudflare Worker に 'HENRIK_API_KEY' が設定されているか確認してください。")
        print("3. Cloudflare Worker が 'valo-reco-api.meld-task.com' に正しく紐付けられ、デプロイされているか確認してください。")

if __name__ == "__main__":
    main()
