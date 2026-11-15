import os
import re
from datetime import datetime
from typing import Optional
from ui.player_utils import resolve_match_player

def is_headshot_kill(kill: dict, match_info: dict) -> bool:
    """キルがヘッドショットによるものかを判定する"""
    if kill.get("headshot") is True or kill.get("is_headshot") is True:
        return True

    for de in kill.get("damage_events") or []:
        if isinstance(de, dict) and de.get("headshots", 0) > 0:
            return True

    killer_puuid = kill.get("killer_puuid")
    victim_puuid = kill.get("victim_puuid")
    killer_name = kill.get("killer_display_name")
    victim_name = kill.get("victim_display_name")

    rounds = match_info.get("rounds") or []
    k_round = kill.get("round")

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

    for r in target_rounds:
        player_stats = r.get("player_stats") or []
        for ps in player_stats:
            p_puuid = ps.get("player_puuid") or ps.get("puuid")
            p_name = ps.get("player_display_name") or ps.get("display_name")
            if (killer_puuid and p_puuid == killer_puuid) or (killer_name and p_name == killer_name):
                for de in ps.get("damage_events") or []:
                    rec_puuid = de.get("receiver_puuid") or de.get("puuid")
                    rec_name = de.get("receiver_display_name") or de.get("display_name")
                    if (victim_puuid and rec_puuid == victim_puuid) or (victim_name and rec_name == victim_name):
                        if de.get("headshots", 0) > 0:
                            return True
    return False

def compute_timeline_base_context(match_info: dict, duration_ms: int = 0) -> dict:
    """タイムライン描画・自動同期・手動基準合わせで共有する基準オフセット(sync_offset=0時)を計算する"""
    recording_start_ms = match_info.get("local_recording_start_time_ms")
    if not recording_start_ms:
        start_time_sec = match_info.get("local_match_start_time", 0)
        recording_start_ms = int(start_time_sec * 1000)

    game_start_sec = float(match_info.get("metadata", {}).get("game_start") or 0)
    api_to_local_offset = int(game_start_sec * 1000)

    kills_data = match_info.get("kills", [])
    raw_local_round_events = match_info.get("local_round_events", [])
    real_match_end_time_ms = match_info.get("local_real_match_end_time_ms")

    mode = str(match_info.get("metadata", {}).get("mode", "")).lower()
    queue = str(match_info.get("metadata", {}).get("queue", "")).lower()
    is_deathmatch = "deathmatch" in mode or "deathmatch" in queue

    game_length = match_info.get("metadata", {}).get("game_length", 0)
    gl_ms = int(game_length if game_length > 300000 else game_length * 1000)
    max_kill_ms = max((k.get("kill_time_in_match", 0) for k in kills_data), default=0)
    effective_match_duration_ms = max(max_kill_ms, gl_ms)

    # 過去バージョンのUTC時差バグ等で動画範囲外にある破損round_eventsを安全に除外
    max_span_ms = max(duration_ms, effective_match_duration_ms, 3600000)
    local_round_events = []
    for ev in raw_local_round_events:
        t = ev.get("time_ms", 0)
        if t < 1000000000000:
            t += recording_start_ms
        if (recording_start_ms - 600000) <= t <= (recording_start_ms + max_span_ms + 600000):
            local_round_events.append(ev)

    local_round_starts = []
    for ev in local_round_events:
        if ev.get("phase") == "InProgress":
            t = ev["time_ms"]
            if t < 1000000000000:
                t += recording_start_ms
            local_round_starts.append(t)

    api_round_starts = []
    for k in kills_data:
        k_match = k.get("kill_time_in_match", 0)
        k_round = k.get("kill_time_in_round", 0)
        if k_match > 0 and k_round > 0:
            r_start = k_match - k_round
            if not api_round_starts or abs(api_round_starts[-1] - r_start) > 5000:
                api_round_starts.append(r_start)

    if is_deathmatch:
        if real_match_end_time_ms and effective_match_duration_ms > 0:
            api_to_local_offset = int(real_match_end_time_ms - effective_match_duration_ms)
        elif local_round_starts and real_match_end_time_ms:
            measured_delay = max(0, (real_match_end_time_ms - local_round_starts[0]) - effective_match_duration_ms)
            api_to_local_offset = int(local_round_starts[0] + measured_delay)
        elif local_round_starts:
            api_to_local_offset = int(local_round_starts[0])
    else:
        if api_round_starts and local_round_starts:
            best_offset = api_to_local_offset
            min_diff = float('inf')

            for l_start in local_round_starts:
                for a_start in api_round_starts:
                    offset = l_start - a_start
                    diff = abs(offset - (game_start_sec * 1000))
                    if diff < 300000 and diff < min_diff:
                        min_diff = diff
                        best_offset = int(offset)

            if min_diff != float('inf'):
                api_to_local_offset = best_offset
        elif real_match_end_time_ms and gl_ms > 0:
            api_to_local_offset = int(real_match_end_time_ms - gl_ms)

    # sync_offset値に依存せずベースオフセット単体で仮救済を判定し、自動・手動同期との基準不整合を防ぐ
    if duration_ms > 0 and kills_data:
        kill_times = [k.get("kill_time_in_match", 0) for k in kills_data if k.get("kill_time_in_match", 0) > 0]
        if kill_times:
            in_bounds = sum(1 for kt in kill_times if 0 <= (kt + api_to_local_offset - recording_start_ms) <= duration_ms)
            if in_bounds == 0 and (max(kill_times) - min(kill_times)) <= duration_ms:
                api_to_local_offset = int(recording_start_ms + (duration_ms // 2) - ((min(kill_times) + max(kill_times)) // 2))

    return {
        "recording_start_ms": int(recording_start_ms),
        "api_to_local_offset": int(api_to_local_offset),
        "base_offset_ms": int(api_to_local_offset - recording_start_ms),
        "mode": mode,
        "is_deathmatch": is_deathmatch,
        "gl_ms": gl_ms,
        "effective_match_duration_ms": effective_match_duration_ms,
        "local_round_events": local_round_events,
        "api_round_starts": api_round_starts,
        "real_match_end_time_ms": real_match_end_time_ms,
    }


def _extract_player_kill_events(
    match_info: dict,
    base_offset_ms: int,
    sync_offset_ms: int,
    riot_id: str,
    tag_line: str,
    known_accounts: Optional[list[str]] = None,
    include_headshot: bool = True,
) -> list[dict]:
    kills_data = match_info.get("kills", [])
    players = match_info.get("players", {}).get("all_players") or []
    resolved_player = resolve_match_player(match_info, riot_id, tag_line, known_accounts)
    target_puuid = resolved_player.get("puuid") if resolved_player else None
    target_display_name = resolved_player.get("name") if resolved_player else None

    result = []
    for kill in kills_data:
        t_api = kill.get("kill_time_in_match", 0)
        time_in_video = int(t_api + base_offset_ms + sync_offset_ms)

        if target_puuid:
            killer_puuid = kill.get("killer_puuid")
            victim_puuid = kill.get("victim_puuid")
            assistants = kill.get("assistants") or []
            assistant_puuids = []
            for ast in assistants:
                if isinstance(ast, dict):
                    assistant_puuids.append(ast.get("assistant_puuid") or "")
                elif isinstance(ast, str):
                    assistant_puuids.append(ast)

            if killer_puuid == target_puuid:
                is_hs = is_headshot_kill(kill, match_info) if include_headshot else False
                result.append({"time": time_in_video, "type": "kill", "headshot": is_hs})
            elif victim_puuid == target_puuid:
                result.append({"time": time_in_video, "type": "death"})
            elif target_puuid in assistant_puuids:
                result.append({"time": time_in_video, "type": "assist"})
        else:
            killer = kill.get("killer_display_name") or "Unknown"
            victim = kill.get("victim_display_name") or "Unknown"
            assistants = kill.get("assistants") or []
            assistant_names = []
            for ast in assistants:
                if isinstance(ast, dict):
                    assistant_names.append(ast.get("assistant_display_name") or "")
                elif isinstance(ast, str):
                    assistant_names.append(ast)

            if target_display_name and target_display_name in killer:
                is_hs = is_headshot_kill(kill, match_info) if include_headshot else False
                result.append({"time": time_in_video, "type": "kill", "headshot": is_hs})
            elif target_display_name and target_display_name in victim:
                result.append({"time": time_in_video, "type": "death"})
            elif target_display_name and any(target_display_name in ast for ast in assistant_names):
                result.append({"time": time_in_video, "type": "assist"})
            elif not players and not target_display_name:
                is_hs = is_headshot_kill(kill, match_info) if include_headshot else False
                result.append({"time": time_in_video, "type": "kill", "headshot": is_hs})
    return result


def extract_player_sync_events(
    match_info: dict,
    duration_ms: int,
    riot_id: str,
    tag_line: str,
    known_accounts: Optional[list[str]] = None,
) -> list[dict]:
    """動画範囲外のイベントも含め、指定イベント基準合わせ用のベース時刻(base_time_ms)一覧を返す"""
    ctx = compute_timeline_base_context(match_info, duration_ms)
    raw_events = _extract_player_kill_events(
        match_info,
        base_offset_ms=ctx["base_offset_ms"],
        sync_offset_ms=0,
        riot_id=riot_id,
        tag_line=tag_line,
        known_accounts=known_accounts,
        include_headshot=False,
    )
    return [{"base_time_ms": ev["time"], "type": ev["type"]} for ev in raw_events]


def build_timeline_data(
    match_info: dict,
    duration_ms: int,
    riot_id: str,
    tag_line: str,
    known_accounts: Optional[list[str]] = None,
) -> tuple[list, list]:
    ctx = compute_timeline_base_context(match_info, duration_ms)
    recording_start_ms = ctx["recording_start_ms"]
    api_to_local_offset = ctx["api_to_local_offset"]
    base_offset_ms = ctx["base_offset_ms"]
    sync_offset_ms = int(match_info.get("timeline_sync_offset_ms") or 0)
    mode = ctx["mode"]
    is_deathmatch = ctx["is_deathmatch"]
    effective_match_duration_ms = ctx["effective_match_duration_ms"]
    local_round_events = ctx["local_round_events"]
    api_round_starts = ctx["api_round_starts"]
    real_match_end_time_ms = ctx["real_match_end_time_ms"]
    local_ability_events = match_info.get("local_ability_events", [])

    events = [
        ev for ev in _extract_player_kill_events(
            match_info,
            base_offset_ms=base_offset_ms,
            sync_offset_ms=sync_offset_ms,
            riot_id=riot_id,
            tag_line=tag_line,
            known_accounts=known_accounts,
            include_headshot=True,
        )
        if 0 <= ev["time"] <= duration_ms + 10000
    ]
    rounds = []

    for ev in local_ability_events:
        t_local = ev["time_ms"]
        if t_local < 1000000000000:
            t_local += recording_start_ms
        time_in_video = int(t_local - recording_start_ms + sync_offset_ms)
        if 0 <= time_in_video <= duration_ms + 10000:
            events.append({"time": time_in_video, "type": "ult"})
            
    if is_deathmatch:
        round_start = max(0, int(api_to_local_offset - recording_start_ms + sync_offset_ms))
        round_end = min(duration_ms, int(api_to_local_offset + effective_match_duration_ms - recording_start_ms + sync_offset_ms))
        if round_end <= round_start:
            round_end = duration_ms
        rounds.append({"start": round_start, "end": round_end, "phase": "Round"})
    elif local_round_events:
        current_round_start = None

        for i, ev in enumerate(local_round_events):
            phase = ev["phase"]
            t_local = ev["time_ms"]
            if t_local < 1000000000000:
                t_local += recording_start_ms
            time_in_video = int(t_local - recording_start_ms + sync_offset_ms)

            if time_in_video < 0:
                time_in_video = 0

            if phase == "InProgress":
                if current_round_start is not None:
                    end_time = min(time_in_video, duration_ms)
                    if current_round_start < end_time:
                        rounds.append({"start": current_round_start, "end": end_time, "phase": "Round"})
                # バリアが降りる瞬間の2秒前をラウンド開始位置とする
                current_round_start = max(0, time_in_video - 2000)

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
            next_buy_ms = 20000 if "spike rush" in mode else 30000
            for i, r_start in enumerate(api_round_starts):
                t_local = r_start + api_to_local_offset
                start_time = int(t_local - recording_start_ms + sync_offset_ms)
                
                # バリアが降りる瞬間の2秒前をラウンド開始位置とする
                start_time = max(0, start_time - 2000)
                
                if i + 1 < len(api_round_starts):
                    next_t_local = api_round_starts[i+1] + api_to_local_offset
                    end_time = int(next_t_local - recording_start_ms + sync_offset_ms) - next_buy_ms
                else:
                    if real_match_end_time_ms:
                        end_time = int(real_match_end_time_ms - recording_start_ms + sync_offset_ms)
                    else:
                        game_length = match_info.get("metadata", {}).get("game_length", 0)
                        gl_ms = game_length if game_length > 300000 else game_length * 1000
                        if gl_ms > 0:
                            end_time = int((gl_ms + api_to_local_offset) - recording_start_ms + sync_offset_ms)
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
