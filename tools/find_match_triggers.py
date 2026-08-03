import os
import re

def find_match_triggers():
    local_app_data = os.environ.get('LOCALAPPDATA')
    if not local_app_data:
        print("エラー: LOCALAPPDATA 環境変数が見つかりません。")
        return

    log_path = os.path.join(local_app_data, 'VALORANT', 'Saved', 'Logs', 'ShooterGame.log')
    
    if not os.path.exists(log_path):
        print(f"エラー: ログファイルが見つかりません。\nパス: {log_path}")
        return

    # 試合の開始・終了・状態遷移に関連する可能性が高いキーワード群
    keywords = [
        r"MatchState",
        r"State Changed",
        r"InProgress",
        r"PreMatch",
        r"PostMatch",
        r"GameMode",
        r"SeamlessTravel",
        r"LoadMap",
        r"Transition"
    ]
    pattern = re.compile("|".join(keywords), re.IGNORECASE)

    output_path = "filtered_match_logs.txt"
    
    print(f"ログを解析中: {log_path}")
    match_count = 0
    
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as fin, \
             open(output_path, 'w', encoding='utf-8') as fout:
            
            for line in fin:
                if pattern.search(line):
                    fout.write(line)
                    match_count += 1
                    
        print(f"抽出完了: {match_count} 行の候補が見つかりました。")
        print(f"結果を '{output_path}' に保存しました。")
        print("このファイルを確認し、試合開始時・終了時に毎回必ず出力される固有の文字列を特定してください。")
        
    except PermissionError:
        print("エラー: ファイルへのアクセス権限がありません。Valorantを終了してから実行してください。")
    except Exception as e:
        print(f"予期せぬエラー: {e}")

if __name__ == "__main__":
    find_match_triggers()