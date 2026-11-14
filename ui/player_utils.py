import os
import re
from datetime import datetime

def find_video_for_json(save_dir: str, json_filename: str, json_data: dict, video_cache: dict = None) -> str:
    match_info = json_data.get("match_info", json_data)
    video_path = match_info.get("local_video_path") or json_data.get("local_video_path")
    
    valid_exts = ('.mp4', '.mkv', '.avi', '.mov', '.webm')
    date_pattern = re.compile(r"(\d{8}_\d{6})")

    if video_path:
        video_path = video_path.replace("\\", "/")
        norm_path = os.path.abspath(video_path)
        if os.path.exists(norm_path):
            return norm_path

        basename = os.path.basename(video_path)
        fallback_path = os.path.join(save_dir, basename)
        if os.path.exists(fallback_path):
            return fallback_path

        # Remux前後や失敗時を考慮し、拡張子違いの同一ベース名ファイルを検索
        base_root, _ = os.path.splitext(basename)
        for ext in valid_exts:
            cand = os.path.join(save_dir, base_root + ext)
            if os.path.exists(cand):
                return cand

    # フォールバック: 複数の時刻ソース候補から最も近い動画を特定する
    candidate_times = []
    if video_path:
        m = date_pattern.search(os.path.basename(video_path))
        if m:
            try:
                candidate_times.append(datetime.strptime(m.group(1), "%Y%m%d_%H%M%S"))
            except ValueError:
                pass

    local_ts = (
        match_info.get("local_match_start_time")
        or json_data.get("local_match_start_time")
        or (
            (match_info.get("local_recording_start_time_ms") or json_data.get("local_recording_start_time_ms"))
            / 1000.0
            if (match_info.get("local_recording_start_time_ms") or json_data.get("local_recording_start_time_ms"))
            else None
        )
    )
    if local_ts:
        try:
            candidate_times.append(datetime.fromtimestamp(float(local_ts)))
        except (ValueError, OSError, OverflowError):
            pass

    json_match = date_pattern.search(json_filename)
    if json_match:
        try:
            candidate_times.append(datetime.strptime(json_match.group(1), "%Y%m%d_%H%M%S"))
        except ValueError:
            pass

    game_start_ts = match_info.get("metadata", {}).get("game_start")
    if game_start_ts:
        try:
            candidate_times.append(datetime.fromtimestamp(float(game_start_ts)))
        except (ValueError, OSError, OverflowError):
            pass

    if not candidate_times:
        return ""

    if video_cache is None:
        video_cache = {}
        try:
            for f in os.listdir(save_dir):
                if f.endswith(valid_exts) and not f.startswith("temp_"):
                    vid_match = date_pattern.search(f)
                    if vid_match:
                        try:
                            video_cache[f] = datetime.strptime(vid_match.group(1), "%Y%m%d_%H%M%S")
                        except ValueError:
                            continue
        except OSError:
            pass

    for target_time in candidate_times:
        best_video = ""
        min_diff = float("inf")
        for f, vid_time in video_cache.items():
            diff = abs((target_time - vid_time).total_seconds())
            if diff < min_diff and diff < 7200:
                min_diff = diff
                best_video = os.path.join(save_dir, f)
        if best_video:
            return best_video

    return ""

def guess_player_name(kills: list) -> str:
    counts = {}
    for k in kills:
        for key in ["killer_display_name", "victim_display_name"]:
            name = k.get(key)
            if name and name != "Unknown":
                base_name = name.split("#")[0]
                counts[base_name] = counts.get(base_name, 0) + 1
        
        for ast in k.get("assistants", []):
            name = ""
            if isinstance(ast, dict):
                name = ast.get("assistant_display_name", "")
            elif isinstance(ast, str):
                name = ast
            if name and name != "Unknown":
                base_name = name.split("#")[0]
                counts[base_name] = counts.get(base_name, 0) + 1
                
    if counts:
        return max(counts, key=counts.get)
    return ""

def get_agent_name(riot_id: str, tag_line: str, match_info: dict, kills_data: list) -> str:
    clean_riot_id = (riot_id or "").strip().lower()
    clean_tag_line = (tag_line or "").strip().lower()
    
    players = match_info.get("players", {}).get("all_players") or []
    
    if clean_riot_id and clean_tag_line:
        for p in players:
            p_name = str(p.get("name") or "").strip().lower()
            p_tag = str(p.get("tag") or "").strip().lower()
            if p_name == clean_riot_id and p_tag == clean_tag_line:
                return p.get("character") or "Unknown Agent"
            
    target_display_name = guess_player_name(kills_data)
    if target_display_name:
        for p in players:
            if p.get("name") == target_display_name:
                return p.get("character") or "Unknown Agent"
                
    return "Unknown Agent"

def get_match_result(riot_id: str, tag_line: str, match_info: dict, kills_data: list) -> str:
    mode = str(match_info.get("metadata", {}).get("mode") or "").lower()
    if mode == "deathmatch":
        return "deathmatch"

    clean_riot_id = (riot_id or "").strip().lower()
    clean_tag_line = (tag_line or "").strip().lower()
    
    target_team = None
    players = match_info.get("players", {}).get("all_players") or []
    
    if clean_riot_id and clean_tag_line:
        for p in players:
            p_name = str(p.get("name") or "").strip().lower()
            p_tag = str(p.get("tag") or "").strip().lower()
            if p_name == clean_riot_id and p_tag == clean_tag_line:
                target_team = str(p.get("team") or "").lower()
                break
            
    if not target_team:
        target_display_name = guess_player_name(kills_data)
        if target_display_name:
            for p in players:
                if p.get("name") == target_display_name:
                    target_team = str(p.get("team") or "").lower()
                    break
                    
    if not target_team:
        return "unknown"
        
    teams = match_info.get("teams") or {}
    team_info = teams.get(target_team) if isinstance(teams, dict) else None
    if team_info:
        has_won = team_info.get("has_won")
        if has_won:
            return "win"
        else:
            other_team = "blue" if target_team == "red" else "red"
            other_team_info = teams.get(other_team, {})
            if not has_won and not other_team_info.get("has_won"):
                return "draw"
            return "loss"
            
    return "unknown"


def _find_target_player(riot_id: str, tag_line: str, match_info: dict, kills_data: list) -> dict:
    clean_riot_id = (riot_id or "").strip().lower()
    clean_tag_line = (tag_line or "").strip().lower()
    players = match_info.get("players", {}).get("all_players") or []

    if clean_riot_id and clean_tag_line:
        for p in players:
            p_name = str(p.get("name") or "").strip().lower()
            p_tag = str(p.get("tag") or "").strip().lower()
            if p_name == clean_riot_id and p_tag == clean_tag_line:
                return p

    target_display_name = guess_player_name(kills_data)
    if target_display_name:
        for p in players:
            if p.get("name") == target_display_name:
                return p

    return {}


def get_match_summary_stats(riot_id: str, tag_line: str, match_info: dict, kills_data: list) -> dict:
    player = _find_target_player(riot_id, tag_line, match_info, kills_data)
    stats = player.get("stats") or {}
    kills = stats.get("kills")
    deaths = stats.get("deaths")
    assists = stats.get("assists")

    kda_str = ""
    if kills is not None and deaths is not None and assists is not None:
        kda_str = f"{kills} / {deaths} / {assists}"

    score_str = ""
    mode = str(match_info.get("metadata", {}).get("mode") or "").lower()
    if mode == "deathmatch":
        if kills is not None:
            score_str = f"{kills} K"
    else:
        target_team = str(player.get("team") or "").lower()
        teams = match_info.get("teams") or {}
        if isinstance(teams, dict) and target_team in ("red", "blue"):
            other_team = "blue" if target_team == "red" else "red"
            my_info = teams.get(target_team) or {}
            other_info = teams.get(other_team) or {}
            my_won = my_info.get("rounds_won")
            other_won = other_info.get("rounds_won")
            if my_won is not None and other_won is not None:
                score_str = f"{my_won} - {other_won}"

    duration_str = ""
    game_length = match_info.get("metadata", {}).get("game_length")
    if isinstance(game_length, (int, float)) and game_length > 0:
        # HenrikAPIのバージョン差異によるミリ秒/秒表現の両方に対応
        total_sec = int(game_length / 1000) if game_length > 10800 else int(game_length)
        if total_sec > 0:
            mins, secs = divmod(total_sec, 60)
            duration_str = f"{mins}:{secs:02d}"

    return {
        "kda": kda_str,
        "score": score_str,
        "duration_str": duration_str,
    }
