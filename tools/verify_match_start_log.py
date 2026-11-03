"""Module: @role: 試合開始から終了までの状態遷移ログを試合単位および分割パートファイルへ抽出するツール"""
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.log_split_utils import get_valorant_log_path, save_split_log_files


def verify_match_start_logs() -> None:
    log_path = get_valorant_log_path()
    if not log_path:
        print("LOCALAPPDATA environment variable not found.")
        return

    if not os.path.exists(log_path):
        print(f"Log file not found: {log_path}")
        return

    keywords = [
        "state", "phase", "inprogress", "preround", "round",
        "match", "warmup", "start", "game",
    ]
    pattern = re.compile("|".join(keywords), re.IGNORECASE)

    is_in_match = False
    match_logs: list[str] = []
    matches_found = 0

    print(f"Reading log file: {log_path}\n")

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                line = raw_line.strip()
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
                    if "LogUI" in line or "LogSlate" in line or "LogAkAudio" in line:
                        continue
                    if pattern.search(line):
                        match_logs.append(line)

        output_path = "match_start_candidates.txt"
        part_files = save_split_log_files(output_path, match_logs)

        print(f"Extraction complete. Found {matches_found} matches ({len(match_logs)} lines).")
        print(f"Saved to '{output_path}' and split into {len(part_files)} pasteable part files:")
        for p in part_files:
            print(f"  - {p}")
    except Exception as e:
        print(f"Error reading log: {e}")


if __name__ == "__main__":
    verify_match_start_logs()
