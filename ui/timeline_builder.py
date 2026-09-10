import os
import re
from datetime import datetime
from ui.player_utils import guess_player_name

def build_timeline_data(match_info: dict, duration_ms: int, riot_id: str, tag_line: str) -> tuple[list, list]:
    # 1. 録画開始のUNIXタイムスタンプ(ms)を取得
    recording_start_ms = match_info.get("local_recording_start_time_ms")
    if not recording_start_ms:
        start_time_sec = match_info.get("local_match_start_time", 0)
        recording_start_ms = int(start_time_sec * 1000)
        
    # 2. API時間(相対)とローカル時間(UNIX絶対)のオフセットを計算
    game_start_sec = match_info.get("metadata", {}).get("game_start", 0)
    api_to_local_offset = game_start_sec * 1000
    
    kills_data = match_info.get("kills", [])
    local_round_events = match_info.get("local_round_events", [])
    local_ability_events = match_info.get("local_ability_events", [])
    real_match_end_time_ms = match_info.get("local_real_match_end_time_ms")
    
    api_round_starts = []
    for k in kills_data:
        k_match = k.get("kill_time_in_match", 0)
        k_round = k.get("kill_time_in_round", 0)
        if k_match > 0 and k_round > 0:
            r_start = k_match - k_round
            if not api_round_starts or abs(api_round_starts[-1] - r_start) > 5000:
                api_round_starts.append(r_start)
                
    local_round_starts = []
    for ev in local_round_events:
        if ev["phase"] == "PreRound":
            t = ev["time_ms"]
            if t < 1000000000000:
                t += recording_start_ms
            local_round_starts.append(t)
            
    # オフセットの決定ロジック
    # 優先度1: ローカルのラウンドイベントとAPIのラウンドイベントの突き合わせ
    if api_round_starts and local_round_starts:
        best_offset = api_to_local_offset
        min_diff = float('inf')
        
        for l_start in local_round_starts:
            for a_start in api_round_starts:
                offset = l_start - a_start
                diff = abs(offset - (game_start_sec * 1000))
                if diff < 300000 and diff < min_diff:
                    min_diff = diff
                    best_offset = offset
                    
        if min_diff != float('inf'):
            api_to_local_offset = best_offset
    # 優先度2: 記録された実際の試合終了時間とAPIの試合時間から逆算
    elif real_match_end_time_ms:
        game_length = match_info.get("metadata", {}).get("game_length", 0)
        # game_length が 300,000 未満なら秒単位とみなしてミリ秒に変換
        gl_ms = game_length if game_length > 300000 else game_length * 1000
        if gl_ms > 0:
            api_to_local_offset = real_match_end_time_ms - gl_ms

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
        t_api = kill.get("kill_time_in_match", 0)
        t_local = t_api + api_to_local_offset
        time_in_video = int(t_local - recording_start_ms)
        
        if time_in_video < 0 or time_in_video > duration_ms + 10000:
            continue
        
        if target_puuid:
            killer_puuid = kill.get("killer_puuid")
            victim_puuid = kill.get("victim_puuid")
            assistants = kill.get("assistants", [])
            assistant_puuids = []
            for ast in assistants:
                if isinstance(ast, dict):
                    assistant_puuids.append(ast.get("assistant_puuid", ""))
                elif isinstance(ast, str):
                    assistant_puuids.append(ast)

            if killer_puuid == target_puuid:
                events.append({"time": time_in_video, "type": "kill"})
            elif victim_puuid == target_puuid:
                events.append({"time": time_in_video, "type": "death"})
            elif target_puuid in assistant_puuids:
                events.append({"time": time_in_video, "type": "assist"})
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
                events.append({"time": time_in_video, "type": "kill"})
            elif target_display_name and target_display_name in victim:
                events.append({"time": time_in_video, "type": "death"})
            elif target_display_name and any(target_display_name in ast for ast in assistant_names):
                events.append({"time": time_in_video, "type": "assist"})
            elif not target_display_name:
                events.append({"time": time_in_video, "type": "kill"})

    for ev in local_ability_events:
        t_local = ev["time_ms"]
        if t_local < 1000000000000:
            t_local += recording_start_ms
        time_in_video = int(t_local - recording_start_ms)
        if 0 <= time_in_video <= duration_ms + 10000:
            events.append({"time": time_in_video, "type": "ult"})
            
    if local_round_events:
        current_round_start = None
        
        for i, ev in enumerate(local_round_events):
            phase = ev["phase"]
            t_local = ev["time_ms"]
            if t_local < 1000000000000:
                t_local += recording_start_ms
            time_in_video = int(t_local - recording_start_ms)
            
            if time_in_video < 0:
                time_in_video = 0
                
            if phase == "PreRound" or (phase == "InProgress" and current_round_start is None):
                if current_round_start is not None:
                    end_time = min(time_in_video, duration_ms)
                    if current_round_start < end_time:
                        rounds.append({"start": current_round_start, "end": end_time, "phase": "Round"})
                current_round_start = time_in_video
                
            elif phase == "PostRound":
                if current_round_start is not None:
                    end_time = min(time_in_video, duration_ms)
                    if current_round_start < end_time:
                        rounds.append({"start": current_round_start, "end": end_time, "phase": "Round"})
                    current_round_start = None
                    
        if current_round_start is not None:
            end_time = duration_ms
            if current_round_start < end_time:
                rounds.append({"start": current_round_start, "end": end_time, "phase": "Round"})
    else:
        if api_round_starts:
            for i, r_start in enumerate(api_round_starts):
                t_local = r_start + api_to_local_offset
                start_time = int(t_local - recording_start_ms)
                
                if i + 1 < len(api_round_starts):
                    next_t_local = api_round_starts[i+1] + api_to_local_offset
                    # 次のラウンド開始の30秒前を終了時間とする（購入フェーズ等を考慮）
                    end_time = int(next_t_local - recording_start_ms) - 30000
                else:
                    if real_match_end_time_ms:
                        end_time = int(real_match_end_time_ms - recording_start_ms)
                    else:
                        game_length = match_info.get("metadata", {}).get("game_length", 0)
                        gl_ms = game_length if game_length > 300000 else game_length * 1000
                        if gl_ms > 0:
                            end_time = int((gl_ms + api_to_local_offset) - recording_start_ms)
                        else:
                            end_time = duration_ms
                
                if start_time < 0:
                    start_time = 0
                if end_time > duration_ms:
                    end_time = duration_ms
                if end_time < start_time:
                    end_time = start_time + 10000 # 最低10秒の長さを確保
                    
                if start_time < end_time:
                    rounds.append({"start": start_time, "end": end_time, "phase": "Round"})
                    
    return rounds, events
