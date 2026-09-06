import os
import json
import subprocess
import re
from datetime import datetime
from ui.player_utils import find_video_for_json, get_agent_name, get_match_result

class RecordDataLoader:
    """
    保存されたJSONファイルからメタデータを読み込み、
    UI表示用に整形・フィルタリングするクラス。
    """
    def __init__(self, config):
        self.config = config

    def load_records(self, current_filter=None):
        if not os.path.exists(self.config.SAVE_DIR):
            return {}, set(), set()
            
        records_by_date = {}
        riot_id = getattr(self.config, "RIOT_ID", "")
        tag_line = getattr(self.config, "TAG_LINE", "")
        
        available_agents = set()
        available_maps = set()
            
        for f in sorted(os.listdir(self.config.SAVE_DIR), reverse=True):
            if f.endswith(".json"):
                json_path = os.path.join(self.config.SAVE_DIR, f)
                try:
                    with open(json_path, 'r', encoding='utf-8') as jf:
                        data = json.load(jf)
                    
                    match_info = data.get("match_info", data)
                    custom_name = data.get("custom_name")
                    is_favorite = data.get("is_favorite", False)
                    is_fetching_api = data.get("is_fetching_api", False)
                    
                    game_start = match_info.get("metadata", {}).get("game_start")
                    if game_start:
                        dt = datetime.fromtimestamp(game_start)
                    else:
                        date_match = re.search(r"(\d{8}_\d{6})", f)
                        if date_match:
                            try:
                                dt = datetime.strptime(date_match.group(1), "%Y%m%d_%H%M%S")
                            except ValueError:
                                dt = datetime.now()
                        else:
                            dt = datetime.now()
                            
                    date_key = dt.strftime('%Y-%m-%d')
                    time_str = dt.strftime('%H:%M')
                    
                    kills_data = match_info.get("kills", [])
                    
                    mode = match_info.get("metadata", {}).get("mode", "Unknown")
                    map_name = match_info.get("metadata", {}).get("map", "Unknown")
                    agent_name = get_agent_name(riot_id, tag_line, match_info, kills_data)
                    result = get_match_result(riot_id, tag_line, match_info, kills_data)
                    
                    available_agents.add(agent_name)
                    available_maps.add(map_name)
                    
                    if current_filter:
                        f_type, f_val = current_filter
                        if f_type == "favorite" and not is_favorite:
                            continue
                        elif f_type == "agent" and agent_name != f_val:
                            continue
                        elif f_type == "result" and result != f_val:
                            continue
                        elif f_type == "map" and map_name != f_val:
                            continue

                    if custom_name:
                        display_name = custom_name
                    else:
                        display_name = f"{mode} - {map_name} - {agent_name} - {date_key} {time_str}"
                    video_path = find_video_for_json(self.config.SAVE_DIR, f, data)
                    
                    thumb_path = ""
                    if video_path and os.path.exists(video_path):
                        thumb_path = os.path.join(self.config.SAVE_DIR, f.replace('.json', '.jpg'))
                        if not os.path.exists(thumb_path):
                            cmd = [
                                "ffmpeg", "-y", "-i", video_path,
                                "-ss", "00:00:01", "-vframes", "1",
                                "-vf", "scale=240:-1", thumb_path
                            ]
                            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
                            
                    if date_key not in records_by_date:
                        records_by_date[date_key] = []
                        
                    mmr_change = data.get("mmr_change", 0)
                    party_members = match_info.get("party_members", [])

                    records_by_date[date_key].append({
                        'filename': f,
                        'display_name': display_name,
                        'thumb_path': thumb_path if os.path.exists(thumb_path) else "",
                        'result': result,
                        'is_favorite': is_favorite,
                        'mmr_change': mmr_change,
                        'party_members': party_members,
                        'is_fetching_api': is_fetching_api
                    })
                    
                except Exception as e:
                    print(f"[RecordDataLoader] Error loading {f}: {e}")
                    
        return records_by_date, available_agents, available_maps
