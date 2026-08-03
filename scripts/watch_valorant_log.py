import os
import time

def watch_valorant_log():
    # WindowsのLOCALAPPDATA環境変数からログディレクトリを特定
    local_app_data = os.environ.get('LOCALAPPDATA')
    if not local_app_data:
        print("エラー: LOCALAPPDATA 環境変数が見つかりません。")
        return

    log_path = os.path.join(local_app_data, 'VALORANT', 'Saved', 'Logs', 'ShooterGame.log')
    
    if not os.path.exists(log_path):
        print(f"エラー: ログファイルが見つかりません。\nパス: {log_path}")
        print("Valorantを起動してから再度実行してください。")
        return

    print(f"監視を開始します: {log_path}")
    print("Valorantをプレイし、出力されるログを確認してください。終了するには Ctrl+C を押します。\n")
    print("-" * 50)
    
    try:
        # Vanguardによるファイルロック時のエラーを防ぐため、読み取り専用・エラー置換モードで開く
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            # 実行前の過去のログは無視し、ファイルの末尾にシークする
            f.seek(0, os.SEEK_END)
            
            while True:
                line = f.readline()
                if not line:
                    # 新しい行がない場合はCPU負荷を下げるために待機
                    time.sleep(0.1)
                    continue
                
                line = line.strip()
                if line:
                    print(line)
                    
                    # 開発メモ: ここで特定のキーワードを検知して処理を分岐させます
                    # 例: 試合開始やマップロードの検知
                    # if "MapLoad" in line or "MatchID" in line:
                    #     print(f">>> [イベント検知] {line}")
                    
    except KeyboardInterrupt:
        print("\n監視を終了しました。")
    except PermissionError:
        print("\nエラー: ファイルへのアクセス権限がありません。Valorantがファイルを排他ロックしている可能性があります。")

if __name__ == "__main__":
    watch_valorant_log()