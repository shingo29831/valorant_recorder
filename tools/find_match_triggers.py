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

    # 抽出したいキーワード群（ゲーム内イベントを広範囲に）
    includes = [
        r"Round", r"Phase", r"Warmup", r"Spawn", r"Timer",
        r"Start", r"Match", r"State", r"Mode", r"HUD", r"Widget",
        r"Score", r"Play", r"Begin", r"Transition"
    ]
    include_pattern = re.compile("|".join(includes), re.IGNORECASE)

    # 除外したいノイズ（大量に出力される不要なログ）
    excludes = [
        r"LogJson", r"LogMapLoadModel", r"LogPlatformSessionManager", 
        r"LogGameFlowStateManager", r"LogAutoTransitionLandingScreenViewModel",
        r"LogLandingScreen", r"LogParty"
    ]
    exclude_pattern = re.compile("|".join(excludes), re.IGNORECASE)

    output_path = "filtered_match_logs_v2.txt"
    
    print(f"ログを解析中: {log_path}")
    match_count = 0
    
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as fin, \
             open(output_path, 'w', encoding='utf-8') as fout:
            
            for line in fin:
                if exclude_pattern.search(line):
                    continue
                if include_pattern.search(line):
                    fout.write(line)
                    match_count += 1
                    
        print(f"抽出完了: {match_count} 行の候補が見つかりました。")
        print(f"結果を '{output_path}' に保存しました。")
        print("このファイルを確認し、ウォームアップ終了・本番開始時に出力される文字列を探してください。")
        
    except PermissionError:
        print("エラー: ファイルへのアクセス権限がありません。Valorantを終了してから実行してください。")
    except Exception as e:
        print(f"予期せぬエラー: {e}")

if __name__ == "__main__":
    find_match_triggers()