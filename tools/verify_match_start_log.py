import os
import re

def verify_match_start_logs():
    local_app_data = os.environ.get('LOCALAPPDATA')
    if not local_app_data:
        print("LOCALAPPDATA environment variable not found.")
        return

    log_path = os.path.join(local_app_data, 'VALORANT', 'Saved', 'Logs', 'ShooterGame.log')
    
    if not os.path.exists(log_path):
        print(f"Log file not found: {log_path}")
        return

    # 抽出したいキーワード（状態遷移、フェーズ、ラウンド、マッチに関連しそうなもの）
    keywords = [
        "state", "phase", "inprogress", "preround", "round", 
        "match", "warmup", "start", "game"
    ]
    pattern = re.compile("|".join(keywords), re.IGNORECASE)

    is_in_match = False
    match_logs = []
    matches_found = 0

    print(f"Reading log file: {log_path}\n")

    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                if "Broadcasting state changed to InGame" in line:
                    is_in_match = True
                    matches_found += 1
                    match_logs.append(f"\n--- MATCH {matches_found} START (InGame) ---")
                    match_logs.append(line)
                    continue
                
                if "Broadcasting state changed to TransitionToMainMenu" in line:
                    if is_in_match:
                        match_logs.append(line)
                        match_logs.append(f"--- MATCH {matches_found} END ---\n")
                    is_in_match = False
                    continue

                if is_in_match:
                    # ログのノイズを減らすため、特定の大量に出るログ（UIの更新など）は除外
                    if "LogUI" in line or "LogSlate" in line or "LogAkAudio" in line:
                        continue
                        
                    if pattern.search(line):
                        match_logs.append(line)

        # 結果をファイルに出力
        output_path = "match_start_candidates.txt"
        with open(output_path, "w", encoding="utf-8") as out_f:
            out_f.write("\n".join(match_logs))
            
        print(f"Extraction complete. Found {matches_found} matches.")
        print(f"Please check '{output_path}' to find the exact log line for match start.")

    except Exception as e:
        print(f"Error reading log: {e}")

if __name__ == "__main__":
    verify_match_start_logs()