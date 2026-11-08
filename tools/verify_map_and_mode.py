"""Module: @role: HenrikAPIとローカルShooterGame.logのマップ名・ゲームモードを抽出し検証・端的に要約出力するツール"""
import argparse
from datetime import datetime
import os
import sys
from typing import Any, Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from api.henrik_api import HenrikAPI
from scripts.get_local_api_info import get_client_region, get_current_player
from tools.log_split_utils import get_valorant_log_path, save_split_log_files
from watcher.log_parser import (
    normalize_map_name,
    parse_game_mode_from_line,
    parse_map_update_from_line,
)


def extract_matches_from_log(log_path: str) -> List[Dict[str, Any]]:
    """ShooterGame.logを走査し、検出された試合セッション情報リストを返す"""
    if not os.path.exists(log_path):
        return []

    matches: List[Dict[str, Any]] = []
    current_match: Optional[Dict[str, Any]] = None
    current_map = "Unknown"
    is_range = False
    current_mode: Optional[str] = None
    buffered_evidence: List[str] = []

    with open(log_path, "r", encoding="utf-8", errors="replace") as fin:
        for raw_line in fin:
            line = raw_line.strip()
            if not line:
                continue

            # マップ更新の追跡
            new_map, new_range, is_updated, is_menu_reset = parse_map_update_from_line(
                line, current_map, is_range
            )
            if is_menu_reset:
                current_map = "Unknown"
                is_range = False
                current_mode = None
            elif is_updated:
                current_map = new_map
                is_range = new_range
                buffered_evidence.append(line)

            # ゲームモードの追跡
            detected_mode = parse_game_mode_from_line(line)
            if detected_mode:
                current_mode = detected_mode
                buffered_evidence.append(line)

            # 試合開始トリガー
            if (
                "Broadcasting state changed to InGame" in line
                or "Match State Changed from WaitingToStart to InProgress" in line
                or "Match State Changed from WaitingToStart to PreRound" in line
            ):
                if current_match is None:
                    current_match = {
                        "map_raw": current_map,
                        "map_normalized": normalize_map_name(current_map),
                        "is_range": is_range,
                        "game_mode": current_mode or ("Range" if is_range else "Unknown"),
                        "start_line": line,
                        "end_line": None,
                        "evidence_lines": list(buffered_evidence[-4:]),
                    }
                    current_match["evidence_lines"].append(line)
                continue

            # 試合終了トリガー
            if (
                "Broadcasting state changed to TransitionToMainMenu" in line
                or "LogGameFlowStateManager: Match ended" in line
                or "Match State Changed to WaitingPostMatch" in line
            ):
                if current_match is not None:
                    current_match["end_line"] = line
                    current_match["evidence_lines"].append(line)
                    matches.append(current_match)
                    current_match = None
                    buffered_evidence.clear()
                continue

    if current_match is not None:
        matches.append(current_match)

    return matches


def fetch_api_latest_match(
    name: Optional[str] = None, tag: Optional[str] = None, region: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """HenrikAPIから最新マッチ情報を取得する（未指定時はローカルクライアントから自動取得）"""
    final_name = name
    final_tag = tag
    final_region = region

    if not final_name or not final_tag:
        local_name, local_tag = get_current_player()
        final_name = final_name or local_name
        final_tag = final_tag or local_tag

    if not final_region:
        final_region = get_client_region() or "ap"

    if not final_name or not final_tag:
        return {
            "player": "Unknown",
            "region": final_region,
            "error": "Could not auto-detect Riot ID / Tag. Please ensure Riot Client is running or specify --name and --tag.",
        }

    try:
        api = HenrikAPI(region=final_region, name=final_name, tag=final_tag)
        latest_match = api.fetch_latest_match(retries=2, delay=3)
        return {
            "player": f"{final_name}#{final_tag}",
            "region": final_region,
            "data": latest_match,
        }
    except Exception as e:
        return {
            "player": f"{final_name}#{final_tag}",
            "region": final_region,
            "error": str(e),
        }


def compare_map_and_mode(
    api_map: Optional[str],
    api_mode: Optional[str],
    log_map: Optional[str],
    log_mode: Optional[str],
) -> tuple[str, str]:
    """マップ名およびゲームモードの比較判定結果文字列を返す"""
    norm_api_map = (api_map or "").strip().lower()
    norm_log_map = (log_map or "").strip().lower()
    if not norm_api_map or not norm_log_map or norm_log_map == "unknown":
        map_verdict = "UNKNOWN"
    elif norm_api_map == norm_log_map:
        map_verdict = "MATCH"
    else:
        map_verdict = "MISMATCH"

    norm_api_mode = (api_mode or "").strip().lower()
    norm_log_mode = (log_mode or "").strip().lower()
    if not norm_api_mode or not norm_log_mode or norm_log_mode == "unknown":
        mode_verdict = "UNKNOWN"
    elif norm_api_mode == norm_log_mode:
        mode_verdict = "MATCH"
    elif norm_log_mode == "standard" and norm_api_mode in ("competitive", "unrated", "custom game"):
        mode_verdict = "PARTIAL (Standard Engine -> Specific Mode)"
    else:
        mode_verdict = "MISMATCH"

    return map_verdict, mode_verdict


def build_verification_summary(
    api_info: Optional[Dict[str, Any]],
    log_matches: List[Dict[str, Any]],
    log_path: str,
) -> str:
    """端的にまとめた検証結果テキストを構築する"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: List[str] = [
        "============================================================",
        f" VALORANT Map & GameMode Verification Summary [{now_str}]",
        "============================================================",
        f"Log Source: {log_path}",
    ]

    api_match_data = (api_info or {}).get("data")
    api_metadata = (api_match_data or {}).get("metadata", {})
    api_map = api_metadata.get("map")
    api_mode = api_metadata.get("mode")
    api_queue = api_metadata.get("queue")
    api_match_id = api_metadata.get("matchid")
    api_start = api_metadata.get("game_start_patched")

    if api_info and "error" in api_info:
        lines.append(f"HenrikAPI Status: ERROR ({api_info['error']}) [Player: {api_info.get('player')}]")
    elif api_info and api_metadata:
        lines.append(f"HenrikAPI Status: SUCCESS [Player: {api_info.get('player')} ({api_info.get('region')})]")
    else:
        lines.append("HenrikAPI Status: NOT CONFIGURED / NO DATA")

    latest_log_match = log_matches[-1] if log_matches else None
    log_map = latest_log_match.get("map_normalized") if latest_log_match else "None"
    log_raw_map = latest_log_match.get("map_raw") if latest_log_match else "None"
    log_mode = latest_log_match.get("game_mode") if latest_log_match else "None"

    map_verdict, mode_verdict = compare_map_and_mode(api_map, api_mode, log_map, log_mode)

    lines.extend([
        "",
        "--- [VERIFICATION COMPARISON (Latest Match)] ---",
        f"Item                 | HenrikAPI               | Local Log (Parsed)      | Verdict",
        f"---------------------+-------------------------+-------------------------+--------",
        f"Map Name             | {str(api_map):<23} | {str(log_map):<23} | {map_verdict}",
        f"Game Mode            | {str(api_mode):<23} | {str(log_mode):<23} | {mode_verdict}",
        f"Queue / Code         | {str(api_queue):<23} | {str(log_raw_map):<23} | -",
        "",
        "--- [HENRIK API DETAILS] ---",
        f"Match ID             : {api_match_id or 'N/A'}",
        f"Game Start (Patched) : {api_start or 'N/A'}",
        f"Rounds Played        : {api_metadata.get('rounds_played', 'N/A')}",
        "",
        "--- [LOCAL LOG EVIDENCE (Latest Match)] ---",
    ])

    if latest_log_match:
        lines.append(f"Raw Map in Log       : {latest_log_match.get('map_raw')}")
        lines.append(f"Normalized Map       : {latest_log_match.get('map_normalized')}")
        lines.append(f"Parsed Game Mode     : {latest_log_match.get('game_mode')}")
        lines.append("Evidence Log Lines:")
        for ev in latest_log_match.get("evidence_lines", []):
            lines.append(f"  > {ev}")
    else:
        lines.append("  (No match sessions found in current ShooterGame.log)")

    lines.extend([
        "",
        f"--- [ALL LOG MATCH SESSIONS ({len(log_matches)} found)] ---",
    ])
    for idx, m in enumerate(log_matches, start=1):
        lines.append(
            f"Session #{idx:02d}: Map={m.get('map_normalized')} (raw: {m.get('map_raw')}) | Mode={m.get('game_mode')}"
        )

    lines.append("============================================================\n")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Map & Mode between HenrikAPI and Local ShooterGame.log")
    parser.add_argument("--log-file", help="Custom ShooterGame.log path", default=None)
    parser.add_argument("--name", help="Riot ID Name", default=None)
    parser.add_argument("--tag", help="Riot ID Tag", default=None)
    parser.add_argument("--region", help="Region (e.g. ap, na, eu)", default=None)
    parser.add_argument("--output", help="Output summary text filename", default="map_mode_verification.txt")
    args = parser.parse_args()

    log_path = args.log_file or get_valorant_log_path()
    if not log_path or not os.path.exists(log_path):
        print(f"[!] Warning: Valorant log file not found at: {log_path}")

    print("[*] Fetching latest match from HenrikAPI...")
    api_info = fetch_api_latest_match(name=args.name, tag=args.tag, region=args.region)

    print(f"[*] Scanning local log file: {log_path} ...")
    log_matches = extract_matches_from_log(log_path) if log_path and os.path.exists(log_path) else []

    summary_text = build_verification_summary(api_info, log_matches, log_path)
    print("\n" + summary_text)

    part_files = save_split_log_files(args.output, summary_text.splitlines())
    print(f"[+] Successfully saved verification summary to: {args.output}")
    for p in part_files:
        print(f"    - Part file: {p}")


if __name__ == "__main__":
    main()
