"""Module: @role: 試合開始・フェーズ遷移のトリガー候補ログを抽出し複数ファイルへ分割保存するツール"""
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.log_split_utils import get_valorant_log_path, save_split_log_files


def find_match_triggers() -> None:
    log_path = get_valorant_log_path()
    if not log_path:
        print("エラー: LOCALAPPDATA 環境変数が見つかりません。")
        return

    if not os.path.exists(log_path):
        print(f"エラー: ログファイルが見つかりません。\nパス: {log_path}")
        return

    includes = [
        r"Round", r"Phase", r"Warmup", r"Spawn", r"Timer",
        r"Start", r"Match", r"State", r"Mode", r"HUD", r"Widget",
        r"Score", r"Play", r"Begin", r"Transition",
    ]
    include_pattern = re.compile("|".join(includes), re.IGNORECASE)

    excludes = [
        r"LogJson", r"LogMapLoadModel", r"LogPlatformSessionManager",
        r"LogGameFlowStateManager", r"LogAutoTransitionLandingScreenViewModel",
        r"LogLandingScreen", r"LogParty",
    ]
    exclude_pattern = re.compile("|".join(excludes), re.IGNORECASE)

    output_path = "filtered_match_logs_v2.txt"
    print(f"ログを解析中: {log_path}")
    matched_lines: list[str] = []

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fin:
            for line in fin:
                if exclude_pattern.search(line):
                    continue
                if include_pattern.search(line):
                    matched_lines.append(line)

        part_files = save_split_log_files(output_path, matched_lines)

        print(f"抽出完了: {len(matched_lines)} 行の候補が見つかりました。")
        print(f"結果を '{output_path}' および {len(part_files)} 個の分割ファイルに保存しました:")
        for p in part_files:
            print(f"  - {p}")
    except PermissionError:
        print("エラー: ファイルへのアクセス権限がありません。Valorantを終了してから実行してください。")
    except Exception as e:
        print(f"予期せぬエラー: {e}")


if __name__ == "__main__":
    find_match_triggers()
