import os
import re
from datetime import datetime
from ui.player_utils import guess_player_name

def build_timeline_data(match_info: dict, duration_ms: int, riot_id: str, tag_line: str) -> tuple[list, list]:
    offset_ms = 0
    local_round_events = match_info.get("local_round_events", [])
    kills_data = match_info.get("kills", [])
    
    api_first_start = 0
    if kills_data:
        first_kill = kills_data[0]
        k_match = first_kill.get("kill_time_in_match", 0)
        k_round = first_kill.get("kill_time_in_round", 0)
        if k_match > 0 and k_round > 0:
            api_first_start = k_match - k_round

    if local_round_events and api_first_start > 0:
        first_local_preround = next((ev for ev in local_round_events if ev["phase"] == "PreRound"), None)
        if first_local_preround:
            offset_ms = api_first_start - first_local_preround["time_ms"]
        else:
            first_local_inprogress = next((ev for ev in local_round_events if ev["phase"] == "InProgress"), None)
            if first_local_inprogress:
                offset_ms = api_first_start - first_local_inprogress["time_ms"]
    elif "local_match_start_time" in match_info and "local_match_end_time" in match_info and duration_ms > 0:
        game_length = match_info.get("metadata", {}).get("game_length")
        if game_length:
            game_length_sec = game_length / 1000.0 if game_length > 100000 else game_length
            end_delay_sec = 6.5
            offset_sec = game_length_sec + end_delay_sec - (duration_ms / 1000.0)
            offset_ms = int(offset_sec * 1000)
        else:
            start_time = match_info["local_match_start_time"]
            end_time = match_info["local_match_end_time"]
            video_zero_local = end_time - (duration_ms / 1000.0)
            offset_sec = video_zero_local - start_time
            offset_ms = int(offset_sec * 1000)
    else:
        if "video_offset_ms" in match_info:
            offset_ms = match_info["video_offset_ms"]
        else:
            video_path = match_info.get("local_video_path", "")
            basename = os.path.basename(video_path)
            date_pattern = re.compile(r"(\d{8}_\d{6})")
            vid_match = date_pattern.search(basename)
            if vid_match:
                try:
                    vid_time = datetime.strptime(vid_match.group(1), "%Y%m%d_%H%M%S")
                    vid_timestamp = vid_time.timestamp()
                    game_start = match_info.get("metadata", {}).get("game_start")
                    if game_start:
                        offset_ms = int((vid_timestamp - game_start) * 1000)
                except Exception:
                    pass

    events = []
    rounds = []
    
    target_puuid = None
    target_display_name = None
    
    players = match_info.get("players", {}).get("all_players", [])
    for p in players:
        p_name = p.get("name", "").lower()
        p_tag = p.get("tag", "").lower()
        if p_name == riot_id and p_tag == tag_line:
            target_puuid = p.get("puuid")
            target_display_name = p.get("name")
            break
    
    if not target_puuid and kills_data:
        target_display_name = guess_player_name(kills_data)
        
    for kill in kills_data:
        time_ms = int(kill.get("kill_time_in_match", 0) - offset_ms)
        if time_ms < 0:
            continue
        
        if target_puuid:
            killer_puuid = kill.get("killer_puuid")
            victim_puuid = kill.get("victim_puuid")
            
            assistants = kill.get("assistants", [])
            assistant_puuids = []
            for ast in assistants:
                if isinstance(ast, dict):
                    assistant_puuids.append(ast.get("assistant_puuid"))
                elif isinstance(ast, str):
                    assistant_puuids.append(ast)
                    
            if killer_puuid == target_puuid:
                events.append({"time": time_ms, "type": "kill"})
            elif victim_puuid == target_puuid:
                events.append({"time": time_ms, "type": "death"})
            elif target_puuid in assistant_puuids:
                events.append({"time": time_ms, "type": "assist"})
        else:
            killer = kill.get("killer_display_name", "Unknown")
            victim = kill.get("victim_display_name", "Unknown")
            
            assistants = kill.get("assistants", [])
            assistant_names = []
            for ast in assistants:
                if isinstance(ast, dict):
                    assistant_names.append(ast.get("assistant_display_name", ""))
                elif isinstance(ast, str):
                    assistant_names.append(ast)
                    
            if target_display_name and target_display_name in killer:
                events.append({"time": time_ms, "type": "kill"})
            elif target_display_name and target_display_name in victim:
                events.append({"time": time_ms, "type": "death"})
            elif target_display_name and any(target_display_name in ast for ast in assistant_names):
                events.append({"time": time_ms, "type": "assist"})
            elif not target_display_name:
                events.append({"time": time_ms, "type": "kill"})
            
    if local_round_events:
        for i in range(len(local_round_events)):
            ev = local_round_events[i]
            phase = ev["phase"]
            start_time = ev["time_ms"]
            end_time = duration_ms
            if i + 1 < len(local_round_events):
                end_time = local_round_events[i+1]["time_ms"]
                
            if phase in ["PreRound", "InProgress", "PostRound"]:
                rounds.append({"start": start_time, "end": end_time, "phase": phase})
    else:
        if kills_data:
            round_starts = []
            for k in kills_data:
                k_match = k.get("kill_time_in_match", 0)
                k_round = k.get("kill_time_in_round", 0)
                if k_match > 0 and k_round > 0:
                    r_start = k_match - k_round
                    if not round_starts or abs(round_starts[-1] - r_start) > 5000:
                        round_starts.append(r_start)
            
            for i, r_start in enumerate(round_starts):
                start = int(r_start - offset_ms)
                if i + 1 < len(round_starts):
                    end = int(round_starts[i+1] - offset_ms)
                else:
                    game_length = match_info.get("metadata", {}).get("game_length", 0)
                    end = int(game_length - offset_ms) if game_length > 0 else duration_ms
                
                if end > start:
                    rounds.append({"start": max(0, start), "end": end, "phase": "InProgress"})
                    
    return rounds, events
