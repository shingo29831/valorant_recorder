import os
import re
from datetime import datetime

def find_video_for_json(save_dir: str, json_filename: str, json_data: dict) -> str:
    match_info = json_data.get("match_info", json_data)
    video_path = match_info.get("local_video_path") or json_data.get("local_video_path")
    
    if video_path:
        video_path = video_path.replace("\\", "/")
        if video_path.startswith("./"):
            abs_path = os.path.abspath(video_path)
            if os.path.exists(abs_path):
                return abs_path
        elif os.path.exists(video_path):
            return os.path.abspath(video_path)
            
        basename = os.path.basename(video_path)
        fallback_path = os.path.join(save_dir, basename)
        if os.path.exists(fallback_path):
            return fallback_path

    date_pattern = re.compile(r"(\d{8}_\d{6})")
    json_match = date_pattern.search(json_filename)
    
    if not json_match:
        return ""
        
    try:
        json_time = datetime.strptime(json_match.group(1), "%Y%m%d_%H%M%S")
    except ValueError:
        return ""

    best_video = ""
    min_diff = float('inf')

    for f in os.listdir(save_dir):
        if f.endswith(('.mp4', '.mkv', '.avi')):
            vid_match = date_pattern.search(f)
            if vid_match:
                try:
                    vid_time = datetime.strptime(vid_match.group(1), "%Y%m%d_%H%M%S")
                    diff = abs((json_time - vid_time).total_seconds())
                    if diff < min_diff and diff < 7200:
                        min_diff = diff
                        best_video = os.path.join(save_dir, f)
                except ValueError:
                    continue
                    
    return best_video

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
    riot_id = riot_id.lower()
    tag_line = tag_line.lower()
    
    players = match_info.get("players", {}).get("all_players", [])
    
    for p in players:
        p_name = p.get("name", "").lower()
        p_tag = p.get("tag", "").lower()
        if p_name == riot_id and p_tag == tag_line:
            return p.get("character", "Unknown Agent")
            
    target_display_name = guess_player_name(kills_data)
    if target_display_name:
        for p in players:
            if p.get("name") == target_display_name:
                return p.get("character", "Unknown Agent")
                
    return "Unknown Agent"

def get_match_result(riot_id: str, tag_line: str, match_info: dict, kills_data: list) -> str:
    riot_id = riot_id.lower()
    tag_line = tag_line.lower()
    
    target_team = None
    players = match_info.get("players", {}).get("all_players", [])
    
    for p in players:
        p_name = p.get("name", "").lower()
        p_tag = p.get("tag", "").lower()
        if p_name == riot_id and p_tag == tag_line:
            target_team = p.get("team", "").lower()
            break
            
    if not target_team:
        target_display_name = guess_player_name(kills_data)
        if target_display_name:
            for p in players:
                if p.get("name") == target_display_name:
                    target_team = p.get("team", "").lower()
                    break
                    
    if not target_team:
        return "unknown"
        
    teams = match_info.get("teams", {})
    team_info = teams.get(target_team)
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