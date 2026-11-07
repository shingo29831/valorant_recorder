"""Module: @role: マッチJSONからキル・ヘッドショット被弾ログを抽出し分割保存するツール"""
import json
import os
import sys
from typing import Any, Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.config import Config
from tools.log_split_utils import save_split_log_files
from ui.timeline_builder import is_headshot_kill


def _find_damage_events(kill: Dict[str, Any], match_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    killer_puuid = kill.get("killer_puuid")
    victim_puuid = kill.get("victim_puuid")
    killer_name = kill.get("killer_display_name")
    victim_name = kill.get("victim_display_name")
    k_round = kill.get("round")

    rounds = match_info.get("rounds") or []
    target_rounds = []
    if isinstance(k_round, int) and 0 <= k_round < len(rounds):
        target_rounds.append(rounds[k_round])
    else:
        for r in rounds:
            if r.get("round_num") == k_round or r.get("round") == k_round:
                target_rounds.append(r)
                break
        if not target_rounds:
            target_rounds = rounds

    matched_events: List[Dict[str, Any]] = []
    for r in target_rounds:
        for ps in r.get("player_stats") or []:
            p_puuid = ps.get("player_puuid") or ps.get("puuid")
            p_name = ps.get("player_display_name") or ps.get("display_name")
            if (killer_puuid and p_puuid == killer_puuid) or (killer_name and p_name == killer_name):
                for de in ps.get("damage_events") or []:
                    rec_puuid = de.get("receiver_puuid") or de.get("puuid")
                    rec_name = de.get("receiver_display_name") or de.get("display_name")
                    if (victim_puuid and rec_puuid == victim_puuid) or (victim_name and rec_name == victim_name):
                        matched_events.append(de)
    return matched_events


def extract_kill_logs(data: Dict[str, Any]) -> List[str]:
    match_info = data.get("match_info", data)
    metadata = match_info.get("metadata", {})
    kills = match_info.get("kills", [])
    rounds = match_info.get("rounds", [])

    lines: List[str] = []
    lines.append("=== Match Summary ===")
    lines.append(f"Match ID   : {metadata.get('matchid', 'Unknown')}")
    lines.append(f"Map / Mode : {metadata.get('map', 'Unknown')} / {metadata.get('mode', 'Unknown')}")
    lines.append(f"Rounds     : {len(rounds)}")
    lines.append(f"Total Kills: {len(kills)}")
    lines.append("")

    if not kills:
        lines.append("※ キルログが存在しません。API取得失敗（ダミーデータ）またはキル未発生の可能性があります。")
        return lines

    hs_count = 0
    kill_lines: List[str] = []

    for idx, kill in enumerate(kills, start=1):
        is_hs = is_headshot_kill(kill, match_info)
        if is_hs:
            hs_count += 1

        t_ms = kill.get("kill_time_in_match", 0)
        sec = t_ms // 1000
        m = sec // 60
        s = sec % 60
        time_str = f"{m:02d}:{s:02d}"

        r_num = kill.get("round", "?")
        killer = kill.get("killer_display_name") or "Unknown"
        victim = kill.get("victim_display_name") or "Unknown"
        weapon = kill.get("damage_weapon_name") or "Unknown"

        status_tag = "[HEADSHOT]" if is_hs else "[BODY/OTHER]"
        kill_lines.append(f"[Kill #{idx:02d} | {time_str}] Round {r_num} - {status_tag}")
        kill_lines.append(f"  Killer: {killer}  -->  Victim: {victim} (Weapon: {weapon})")

        damage_events = _find_damage_events(kill, match_info)
        if damage_events:
            kill_lines.append("  Damage Breakdown (from rounds.player_stats.damage_events):")
            for de in damage_events:
                hs = de.get("headshots", 0)
                bs = de.get("bodyshots", 0)
                ls = de.get("legshots", 0)
                dmg = de.get("damage", 0)
                kill_lines.append(f"    - Head: {hs}, Body: {bs}, Leg: {ls} | Total Damage: {dmg}")
        else:
            kill_lines.append("  Damage Breakdown: (該当ラウンドの被弾イベントなし)")
        kill_lines.append("")

    hs_rate = (hs_count / len(kills) * 100) if kills else 0.0
    lines.append(f"Headshot Kills: {hs_count} / {len(kills)} ({hs_rate:.1f}%)")
    lines.append("-" * 60)
    lines.extend(kill_lines)
    return lines


def main() -> None:
    filepath = sys.argv[1] if len(sys.argv) > 1 else None

    if not filepath:
        cfg = Config()
        if os.path.exists(cfg.SAVE_DIR):
            candidates = [
                os.path.join(cfg.SAVE_DIR, f)
                for f in os.listdir(cfg.SAVE_DIR)
                if f.startswith("match_") and f.endswith(".json")
            ]
            if candidates:
                candidates.sort(key=os.path.getmtime, reverse=True)
                filepath = candidates[0]

    if not filepath or not os.path.exists(filepath):
        print("Usage: python tools/split_match_kill_log.py <path_to_match.json>")
        print("※ 引数を省略した場合は最新の保存済みマッチJSONを自動検出します。")
        return

    print(f"Reading match data from: {filepath}")
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)

    lines = extract_kill_logs(data)
    output_base = "match_kills_extracted.txt"
    saved_parts = save_split_log_files(output_base, lines)

    print("Done! Extracted kill and headshot logs into pasteable parts:")
    print(f" -> Base file: {output_base}")
    for part in saved_parts:
        print(f"    * {part}")


if __name__ == "__main__":
    main()
