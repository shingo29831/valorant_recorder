"""Module: @role: 録画データの単体・一括ログ再取得（HenrikAPI照合およびローカルShooterGame.log補完）を担うモジュール"""
from datetime import datetime
import json
import os
import re
import time
from typing import Optional
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QWidget
from api.henrik_api import HenrikAPI
from core.config import Config
from storage.metadata_store import MetadataStore
from ui.player_utils import find_video_for_json
from ui.watcher_bg_service import NON_MMR_MODES, MatchMetadataService
from watcher.log_parser import (
    END_LOG_PATTERNS,
    IGNORED_MODULE_PATTERN,
    RELEVANT_LOG_KEYWORDS,
    TIME_PATTERN,
    normalize_map_name,
    parse_game_mode_from_line,
    parse_log_timestamp,
    parse_map_update_from_line,
    parse_state_transition,
)

_DATE_FILENAME_RE = re.compile(r"(\d{8}_\d{6})")


def set_record_fetching_state(save_dir: str, json_filename: str, is_fetching: bool) -> None:
    json_path = os.path.join(save_dir, json_filename)
    if not os.path.exists(json_path):
        return
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["is_fetching_api"] = is_fetching
        if isinstance(data.get("match_info"), dict):
            data["match_info"]["is_fetching_api"] = is_fetching
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except OSError:
        pass


def set_multiple_records_fetching_state(
    save_dir: str, json_filenames: list[str], is_fetching: bool
) -> None:
    for fname in json_filenames:
        set_record_fetching_state(save_dir, fname, is_fetching)


def _resolve_record_time_bounds(
    save_dir: str, json_filename: str, data: dict, match_info: dict
) -> tuple[str, float, float]:
    video_path = find_video_for_json(save_dir, json_filename, data)
    start_time = float(
        match_info.get("local_match_start_time")
        or data.get("local_match_start_time")
        or (
            (
                match_info.get("local_recording_start_time_ms")
                or data.get("local_recording_start_time_ms")
                or 0
            )
            / 1000.0
        )
        or 0.0
    )
    if start_time <= 0:
        for candidate in (os.path.basename(video_path or ""), json_filename):
            m = _DATE_FILENAME_RE.search(candidate)
            if m:
                try:
                    start_time = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S").timestamp()
                    break
                except ValueError:
                    pass
    if start_time <= 0:
        start_time = float(match_info.get("metadata", {}).get("game_start") or time.time())

    end_time = float(
        match_info.get("local_match_end_time")
        or data.get("local_match_end_time")
        or (
            (
                match_info.get("local_real_match_end_time_ms")
                or data.get("local_real_match_end_time_ms")
                or 0
            )
            / 1000.0
        )
        or 0.0
    )
    if end_time <= start_time and video_path and os.path.exists(video_path):
        try:
            mtime = os.path.getmtime(video_path)
            if mtime > start_time:
                end_time = mtime
        except OSError:
            pass
    if end_time <= start_time:
        raw_len = float(match_info.get("metadata", {}).get("game_length") or 0)
        len_sec = raw_len / 1000.0 if raw_len > 10800 else raw_len
        end_time = start_time + (len_sec if len_sec > 0 else 1800.0)

    return video_path, start_time, end_time


def _extract_events_from_local_logs(start_time: float, end_time: float) -> dict:
    result = {
        "map_name": "",
        "mode": "",
        "round_events": [],
        "ability_events": [],
        "real_match_end_time_ms": None,
    }
    local_app_data = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local"
    )
    log_dir = os.path.join(local_app_data, "VALORANT", "Saved", "Logs")
    if not os.path.isdir(log_dir):
        return result

    log_files = []
    try:
        for fname in os.listdir(log_dir):
            if fname.startswith("ShooterGame") and fname.endswith(".log"):
                fpath = os.path.join(log_dir, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                    if mtime >= start_time - 600:
                        log_files.append((mtime, fpath))
                except OSError:
                    continue
    except OSError:
        return result

    log_files.sort(key=lambda x: x[0])
    win_start = start_time - 180.0
    win_end = end_time + 180.0

    for file_mtime, fpath in log_files:
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as lf:
                lines = lf.readlines()
        except OSError:
            continue
        if not lines:
            continue

        # UEのUTCログ出力時刻とOSローカルエポック秒の時差をファイル末尾時刻から自動補正
        tz_offset = 0.0
        for rev_line in reversed(lines[-200:]):
            if TIME_PATTERN.match(rev_line):
                last_raw_ts = parse_log_timestamp(rev_line)
                tz_offset = round((file_mtime - last_raw_ts) / 1800.0) * 1800.0
                break

        cur_map = "Unknown"
        is_range = False
        for raw_line in lines:
            line = raw_line.strip()
            if not line or not TIME_PATTERN.match(line):
                continue
            ts = parse_log_timestamp(line) + tz_offset
            if ts < win_start or ts > win_end:
                continue

            cur_map, is_range, updated_map, _ = parse_map_update_from_line(line, cur_map, is_range)
            if updated_map and not is_range and cur_map not in ("Unknown", ""):
                result["map_name"] = normalize_map_name(cur_map)

            parsed_mode = parse_game_mode_from_line(line)
            if parsed_mode and parsed_mode != "The Range":
                result["mode"] = parsed_mode

            if not any(k in line for k in RELEVANT_LOG_KEYWORDS) or IGNORED_MODULE_PATTERN.search(line):
                continue

            _, to_state = parse_state_transition(line)
            ts_ms = int(ts * 1000)
            if to_state in ("PreRound", "InProgress", "PostRound"):
                result["round_events"].append({"phase": to_state, "time_ms": ts_ms})
            if "LogAbilitySystem:" in line and "Ability activated" in line:
                result["ability_events"].append({"time_ms": ts_ms})
            if any(p in line for p in END_LOG_PATTERNS) or to_state in ("WaitingPostMatch", "PostGame"):
                result["real_match_end_time_ms"] = ts_ms

    return result


def _compute_match_time_diff(
    api_match: dict, start_time: float, end_time: float
) -> Optional[float]:
    meta = api_match.get("metadata") or {}
    game_start = float(meta.get("game_start") or 0)
    if game_start <= 0:
        return None
    raw_len = float(meta.get("game_length") or 0)
    game_len_sec = raw_len / 1000.0 if raw_len > 10800 else raw_len
    game_end = game_start + max(game_len_sec, 300.0)

    rec_end = max(start_time + 60.0, end_time)
    diff = abs(game_start - start_time)
    overlaps = (start_time - 900.0) <= game_end and (rec_end + 600.0) >= game_start
    if diff <= 2400.0 or (overlaps and diff <= 5400.0):
        return diff
    return None


def _find_best_matching_api_match(
    matches: list[dict],
    start_time: float,
    end_time: float,
    existing_match_id: str,
    riot_id: str = "",
    tag_line: str = "",
) -> Optional[dict]:
    if existing_match_id and not existing_match_id.startswith(("custom_", "pending_", "recovered_")):
        for m in matches:
            if str(m.get("metadata", {}).get("matchid") or "") == existing_match_id:
                if not riot_id or not tag_line or MatchMetadataService.is_player_in_match(m, riot_id, tag_line):
                    return m

    best_match = None
    best_diff = float("inf")
    for m in matches:
        if riot_id and tag_line and not MatchMetadataService.is_player_in_match(m, riot_id, tag_line):
            continue
        diff = _compute_match_time_diff(m, start_time, end_time)
        if diff is not None and diff < best_diff:
            best_diff = diff
            best_match = m
    return best_match


def _resolve_player_identity(config: Config) -> tuple[str, str, str]:
    riot_id = (getattr(config, "RIOT_ID", "") or "").strip()
    tag_line = (getattr(config, "TAG_LINE", "") or "").strip()
    region = (getattr(config, "REGION", "ap") or "ap").strip() or "ap"

    try:
        from scripts.get_local_api_info import get_client_region, get_current_player

        det_name, det_tag = get_current_player()
        det_region = get_client_region()
        if det_name and det_tag:
            riot_id = det_name
            tag_line = det_tag
            region = det_region or region
            changed = False
            if hasattr(config, "register_known_account") and config.register_known_account(riot_id, tag_line):
                changed = True
            if config.RIOT_ID != riot_id or config.TAG_LINE != tag_line or config.REGION != region:
                config.RIOT_ID = riot_id
                config.TAG_LINE = tag_line
                config.REGION = region
                changed = True
            if changed:
                config.save()
        elif det_region and config.REGION != det_region:
            region = det_region
            config.REGION = region
            config.save()
    except Exception:
        pass

    return riot_id, tag_line, region


def _collect_account_candidates(
    config: Config,
    primary_riot_id: str,
    primary_tag_line: str,
    primary_region: str,
    match_info: Optional[dict] = None,
) -> list[tuple[str, str, str]]:
    candidates: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(name: str, tag: str, reg: str) -> None:
        c_name = (name or "").strip()
        c_tag = (tag or "").strip()
        if not c_name or not c_tag:
            return
        key = (c_name.lower(), c_tag.lower())
        if key not in seen:
            seen.add(key)
            candidates.append((c_name, c_tag, (reg or primary_region or "ap").strip() or "ap"))

    if isinstance(match_info, dict):
        rec_p = match_info.get("recorded_player")
        if isinstance(rec_p, dict):
            _add(
                str(rec_p.get("name") or ""),
                str(rec_p.get("tag") or ""),
                str(rec_p.get("region") or primary_region),
            )
        party_members = [m for m in (match_info.get("party_members") or []) if isinstance(m, str) and "#" in m]
        if len(party_members) == 1:
            p_name, p_tag = party_members[0].rsplit("#", 1)
            _add(p_name, p_tag, primary_region)

    _add(primary_riot_id, primary_tag_line, primary_region)

    for entry in getattr(config, "KNOWN_ACCOUNTS", []) or []:
        if isinstance(entry, str) and "#" in entry:
            k_name, k_tag = entry.rsplit("#", 1)
            _add(k_name, k_tag, primary_region)

    return candidates


def _are_events_near_window(events: list, start_time: float, end_time: float) -> bool:
    if not events:
        return False
    win_lo = (start_time - 3600.0) * 1000.0
    win_hi = (max(start_time + 3600.0, end_time) + 3600.0) * 1000.0
    for ev in events:
        t = ev.get("time_ms", 0)
        if t < 1000000000000 or (win_lo <= t <= win_hi):
            return True
    return False


def _apply_matched_data_to_record(
    config: Config,
    json_path: str,
    json_filename: str,
    data: dict,
    match_info: dict,
    matched_data: Optional[dict],
    mmr_change: int,
    riot_id: str,
    tag_line: str,
    video_path: str,
    start_time: float,
    end_time: float,
    region: str = "",
) -> tuple[bool, str, bool]:
    custom_name = data.get("custom_name")
    is_favorite = data.get("is_favorite", False)
    sync_offset_ms = match_info.get("timeline_sync_offset_ms")
    sync_source = match_info.get("timeline_sync_source")
    local_round_events = list(match_info.get("local_round_events") or [])
    local_ability_events = list(match_info.get("local_ability_events") or [])
    start_time_ms = match_info.get("local_recording_start_time_ms")
    real_match_end_time_ms = match_info.get("local_real_match_end_time_ms")

    # 過去バージョンのUTC時差バグ等で動画時刻から大きく逸脱した壊れたローカルイベントを排除
    if local_round_events and not _are_events_near_window(local_round_events, start_time, end_time):
        local_round_events = []
    if local_ability_events and not _are_events_near_window(local_ability_events, start_time, end_time):
        local_ability_events = []

    log_extracted = _extract_events_from_local_logs(start_time, end_time)
    if not local_round_events and log_extracted["round_events"]:
        local_round_events = log_extracted["round_events"]
    if not local_ability_events and log_extracted["ability_events"]:
        local_ability_events = log_extracted["ability_events"]
    if real_match_end_time_ms is None and log_extracted["real_match_end_time_ms"] is not None:
        real_match_end_time_ms = log_extracted["real_match_end_time_ms"]

    if matched_data is not None:
        api_copy = dict(matched_data)
        rec_player = MatchMetadataService.build_recorded_player_info(api_copy, riot_id, tag_line, region)
        if rec_player:
            api_copy["recorded_player"] = rec_player
        party_members = MatchMetadataService._extract_party_members(api_copy, riot_id, tag_line)
        api_copy["party_members"] = party_members
        api_copy["local_video_path"] = video_path
        api_copy["local_match_start_time"] = start_time
        api_copy["local_match_end_time"] = end_time
        api_copy["local_round_events"] = local_round_events
        api_copy["local_ability_events"] = local_ability_events
        api_copy["is_fetching_api"] = False
        if start_time_ms is not None:
            api_copy["local_recording_start_time_ms"] = start_time_ms
        if real_match_end_time_ms is not None:
            api_copy["local_real_match_end_time_ms"] = real_match_end_time_ms
        if sync_offset_ms is not None and sync_source == "manual":
            api_copy["timeline_sync_offset_ms"] = sync_offset_ms
            api_copy["timeline_sync_source"] = sync_source

        store = MetadataStore(config)
        new_filepath = store.save_match_metadata(api_copy, mmr_change)

        if custom_name or is_favorite:
            try:
                with open(new_filepath, "r", encoding="utf-8") as nf:
                    saved_payload = json.load(nf)
                if custom_name:
                    saved_payload["custom_name"] = custom_name
                if is_favorite:
                    saved_payload["is_favorite"] = True
                with open(new_filepath, "w", encoding="utf-8") as nf:
                    json.dump(saved_payload, nf, ensure_ascii=False, indent=4)
            except OSError:
                pass

        new_filename = os.path.basename(new_filepath)
        if os.path.abspath(new_filepath) != os.path.abspath(json_path):
            old_thumb = json_path.replace(".json", ".jpg")
            new_thumb = new_filepath.replace(".json", ".jpg")
            if os.path.exists(old_thumb) and not os.path.exists(new_thumb):
                try:
                    os.replace(old_thumb, new_thumb)
                except OSError:
                    pass
            try:
                if os.path.exists(json_path):
                    os.remove(json_path)
            except OSError:
                pass

        return True, new_filename, True

    updated_local = False
    meta = match_info.setdefault("metadata", {})
    if log_extracted["map_name"] and meta.get("map") in ("Unknown", "Custom / Unknown", "Fetching...", None):
        meta["map"] = log_extracted["map_name"]
        updated_local = True
    if log_extracted["mode"] and meta.get("mode") in ("Unknown", "Custom", None):
        meta["mode"] = log_extracted["mode"]
        updated_local = True
    if local_round_events and not match_info.get("local_round_events"):
        match_info["local_round_events"] = local_round_events
        updated_local = True
    if local_ability_events and not match_info.get("local_ability_events"):
        match_info["local_ability_events"] = local_ability_events
        updated_local = True
    if real_match_end_time_ms is not None and match_info.get("local_real_match_end_time_ms") is None:
        match_info["local_real_match_end_time_ms"] = real_match_end_time_ms
        updated_local = True

    match_info["is_fetching_api"] = False
    data["is_fetching_api"] = False
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except OSError:
        pass

    return updated_local, json_filename, False


def refetch_record_log(config: Config, json_filename: str, is_ja: bool = True) -> tuple[bool, str, str]:
    save_dir = config.SAVE_DIR
    json_path = os.path.join(save_dir, json_filename)
    if not os.path.exists(json_path):
        msg = "対象のメタデータファイルが見つかりません" if is_ja else "Metadata file not found"
        return False, json_filename, msg

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return False, json_filename, str(e)

    match_info = data.get("match_info", data)
    video_path, start_time, end_time = _resolve_record_time_bounds(
        save_dir, json_filename, data, match_info
    )

    riot_id, tag_line, region = _resolve_player_identity(config)
    if not riot_id or not tag_line:
        set_record_fetching_state(save_dir, json_filename, False)
        err = (
            "Riot IDとタグラインが未設定です。設定画面で入力するかVALORANTを起動してください"
            if is_ja
            else "Riot ID and Tag Line are not configured."
        )
        return False, json_filename, err

    existing_match_id = str(match_info.get("metadata", {}).get("matchid") or "")
    api_error_str = ""
    matched_data = None
    mmr_change = int(data.get("mmr_change") or 0)
    used_riot_id, used_tag_line, used_region = riot_id, tag_line, region

    account_candidates = _collect_account_candidates(config, riot_id, tag_line, region, match_info)
    for cand_id, cand_tag, cand_region in account_candidates:
        try:
            api = HenrikAPI(cand_region, cand_id, cand_tag)
            matches = api.fetch_matches(size=10, retries=2, delay=2)
            cand_match = _find_best_matching_api_match(
                matches, start_time, end_time, existing_match_id, cand_id, cand_tag
            )
            if cand_match:
                matched_data = cand_match
                used_riot_id, used_tag_line, used_region = cand_id, cand_tag, cand_region
                match_id = matched_data["metadata"]["matchid"]
                mode = str(matched_data.get("metadata", {}).get("mode") or "")
                if mode.lower() not in NON_MMR_MODES:
                    try:
                        mmr_change = api.fetch_mmr_change(match_id, retries=2, delay=2)
                    except Exception:
                        pass
                break
        except Exception as e:
            if not api_error_str:
                api_error_str = str(e)

    updated, new_filename, is_api_matched = _apply_matched_data_to_record(
        config,
        json_path,
        json_filename,
        data,
        match_info,
        matched_data,
        mmr_change,
        used_riot_id,
        used_tag_line,
        video_path,
        start_time,
        end_time,
        region=used_region,
    )

    if is_api_matched:
        msg = "✅ 試合ログを再取得しました" if is_ja else "✅ Successfully refetched match log."
        return True, new_filename, msg

    if updated:
        msg = (
            "ローカルログからマップ・ラウンド情報を復元しました（APIには該当試合が見つかりませんでした）"
            if is_ja
            else "Restored map/round info from local logs (match not found on API)."
        )
        return True, new_filename, msg

    if api_error_str:
        err = f"⚠️ API通信エラー ({api_error_str})" if is_ja else f"⚠️ API error ({api_error_str})"
    else:
        err = (
            "⚠️ APIの直近履歴に該当する試合データが見つかりませんでした"
            if is_ja
            else "⚠️ Matching game data was not found in recent API history."
        )
    return False, new_filename, err


def batch_refetch_record_logs(
    config: Config, json_filenames: list[str], is_ja: bool = True
) -> tuple[int, int, str]:
    save_dir = config.SAVE_DIR
    total = len(json_filenames)
    if total == 0:
        msg = "未取得のログはありません" if is_ja else "No records with missing logs found."
        return 0, 0, msg

    riot_id, tag_line, region = _resolve_player_identity(config)
    if not riot_id or not tag_line:
        set_multiple_records_fetching_state(save_dir, json_filenames, False)
        err = (
            "⚠️ Riot IDとタグラインが未設定です。設定画面で入力するかVALORANTを起動してください"
            if is_ja
            else "⚠️ Riot ID and Tag Line are not configured."
        )
        return 0, total, err

    records_ctx = []
    for fname in json_filenames:
        jpath = os.path.join(save_dir, fname)
        if not os.path.exists(jpath):
            continue
        try:
            with open(jpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            minfo = data.get("match_info", data)
            vpath, stime, etime = _resolve_record_time_bounds(save_dir, fname, data, minfo)
            records_ctx.append({
                "filename": fname,
                "json_path": jpath,
                "data": data,
                "match_info": minfo,
                "video_path": vpath,
                "start_time": stime,
                "end_time": etime,
                "existing_match_id": str(minfo.get("metadata", {}).get("matchid") or ""),
            })
        except Exception:
            set_record_fetching_state(save_dir, fname, False)

    if not records_ctx:
        set_multiple_records_fetching_state(save_dir, json_filenames, False)
        msg = "対象のメタデータファイルが見つかりません" if is_ja else "Metadata files not found."
        return 0, total, msg

    api_error_str = ""
    assigned_matches: dict[int, tuple[dict, str, str, str]] = {}
    used_match_ids: set[str] = set()
    mmr_maps_by_account: dict[tuple[str, str], dict[str, int]] = {}

    account_candidates = _collect_account_candidates(config, riot_id, tag_line, region)
    for cand_id, cand_tag, cand_region in account_candidates:
        if len(assigned_matches) >= len(records_ctx):
            break
        try:
            api = HenrikAPI(cand_region, cand_id, cand_tag)
            matches = api.fetch_matches(size=10, retries=2, delay=2)
        except Exception as e:
            if not api_error_str:
                api_error_str = str(e)
            continue

        if not matches:
            continue

        valid_matches = [m for m in matches if MatchMetadataService.is_player_in_match(m, cand_id, cand_tag)]
        if not valid_matches:
            continue

        newly_assigned = []
        for idx, rctx in enumerate(records_ctx):
            if idx in assigned_matches:
                continue
            ex_id = rctx["existing_match_id"]
            if ex_id and not ex_id.startswith(("custom_", "pending_", "recovered_")):
                for m in valid_matches:
                    mid = str(m.get("metadata", {}).get("matchid") or "")
                    if mid == ex_id:
                        assigned_matches[idx] = (m, cand_id, cand_tag, cand_region)
                        used_match_ids.add(mid)
                        newly_assigned.append(m)
                        break

        candidate_pairs = []
        for idx, rctx in enumerate(records_ctx):
            if idx in assigned_matches:
                continue
            for m_idx, m in enumerate(valid_matches):
                diff = _compute_match_time_diff(m, rctx["start_time"], rctx["end_time"])
                if diff is not None:
                    candidate_pairs.append((diff, idx, m_idx))

        candidate_pairs.sort(key=lambda x: x[0])
        for _diff, idx, m_idx in candidate_pairs:
            if idx in assigned_matches:
                continue
            m = valid_matches[m_idx]
            mid = str(m.get("metadata", {}).get("matchid") or "")
            if mid and mid not in used_match_ids:
                assigned_matches[idx] = (m, cand_id, cand_tag, cand_region)
                used_match_ids.add(mid)
                newly_assigned.append(m)

        # 分割録画等で同一試合に複数動画が対応する場合のフォールバック割当
        for _diff, idx, m_idx in candidate_pairs:
            if idx not in assigned_matches:
                m = valid_matches[m_idx]
                assigned_matches[idx] = (m, cand_id, cand_tag, cand_region)
                newly_assigned.append(m)

        need_mmr = any(
            str(m.get("metadata", {}).get("mode") or "").lower() not in NON_MMR_MODES
            for m in newly_assigned
        )
        if need_mmr:
            try:
                mmr_maps_by_account[(cand_id.lower(), cand_tag.lower())] = api.fetch_mmr_history_map(
                    retries=2, delay=2
                )
            except Exception:
                mmr_maps_by_account[(cand_id.lower(), cand_tag.lower())] = {}

    success_count = 0
    for idx, rctx in enumerate(records_ctx):
        assigned = assigned_matches.get(idx)
        matched_data = assigned[0] if assigned else None
        used_id = assigned[1] if assigned else riot_id
        used_tag = assigned[2] if assigned else tag_line
        used_reg = assigned[3] if assigned else region

        mmr_change = int(rctx["data"].get("mmr_change") or 0)
        if matched_data is not None:
            mid = str(matched_data.get("metadata", {}).get("matchid") or "")
            mode = str(matched_data.get("metadata", {}).get("mode") or "")
            acc_mmr_map = mmr_maps_by_account.get((used_id.lower(), used_tag.lower()), {})
            if mode.lower() not in NON_MMR_MODES and mid in acc_mmr_map:
                mmr_change = acc_mmr_map[mid]

        updated, _new_fname, _is_api = _apply_matched_data_to_record(
            config,
            rctx["json_path"],
            rctx["filename"],
            rctx["data"],
            rctx["match_info"],
            matched_data,
            mmr_change,
            used_id,
            used_tag,
            rctx["video_path"],
            rctx["start_time"],
            rctx["end_time"],
            region=used_reg,
        )
        if updated:
            success_count += 1

    if success_count > 0:
        msg = (
            f"✅ {success_count} / {total} 件の試合ログを再取得しました"
            if is_ja
            else f"✅ Refetched logs for {success_count} / {total} record(s)."
        )
    elif api_error_str:
        msg = (
            f"⚠️ 一括取得失敗: API通信エラー ({api_error_str})"
            if is_ja
            else f"⚠️ Batch refetch failed: API error ({api_error_str})"
        )
    else:
        msg = (
            f"⚠️ 該当する試合データがAPIに見つかりませんでした (0 / {total} 件)"
            if is_ja
            else f"⚠️ No matching game data found in recent API history (0 / {total})."
        )
    return success_count, total, msg


class LogRefetchThread(QThread):
    finishedRefetch = pyqtSignal(str, bool, str, str)

    def __init__(
        self, config: Config, json_filename: str, is_ja: bool = True, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.json_filename = json_filename
        self.is_ja = is_ja

    def run(self) -> None:
        success, new_filename, message = refetch_record_log(
            self.config, self.json_filename, is_ja=self.is_ja
        )
        self.finishedRefetch.emit(self.json_filename, success, new_filename, message)


class BatchLogRefetchThread(QThread):
    finishedBatchRefetch = pyqtSignal(int, int, str)

    def __init__(
        self,
        config: Config,
        json_filenames: list[str],
        is_ja: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.json_filenames = list(json_filenames)
        self.is_ja = is_ja

    def run(self) -> None:
        success_count, total_count, message = batch_refetch_record_logs(
            self.config, self.json_filenames, is_ja=self.is_ja
        )
        self.finishedBatchRefetch.emit(success_count, total_count, message)
