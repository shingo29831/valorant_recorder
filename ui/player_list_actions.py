"""Module: @role: 録画一覧のフィルタ・ソートメニュー構築およびレコード削除・お気に入り切替・リネーム・ログ再取得操作を担うモジュール"""
from datetime import datetime
import json
import os
import re
import time
from typing import Callable, Optional
from PyQt6.QtCore import QPoint, QThread, pyqtSignal
from PyQt6.QtWidgets import QInputDialog, QMenu, QPushButton, QWidget
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


def toggle_favorite_status(save_dir: str, json_filename: str) -> bool:
    json_path = os.path.join(save_dir, json_filename)
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        new_state = not data.get("is_favorite", False)
        data["is_favorite"] = new_state
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return new_state
    except Exception as e:
        print(f"[PlayerListActions] Error updating favorite status: {e}")
        return False


def reveal_record_in_explorer(save_dir: str, json_filename: str) -> None:
    import subprocess
    json_path = os.path.join(save_dir, json_filename)
    target_path = json_path
    try:
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            video_path = find_video_for_json(save_dir, json_filename, data)
            if video_path and os.path.exists(video_path):
                target_path = video_path
    except Exception:
        pass

    norm_target = os.path.normpath(target_path)
    if os.name == "nt" and os.path.exists(norm_target):
        subprocess.Popen(["explorer", "/select,", norm_target])
    elif os.path.exists(save_dir) and os.name == "nt":
        os.startfile(os.path.normpath(save_dir))


def delete_single_record_files(save_dir: str, json_filename: str) -> None:
    json_path = os.path.join(save_dir, json_filename)
    try:
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            video_path = find_video_for_json(save_dir, json_filename, data)
            thumb_path = json_path.replace(".json", ".jpg")

            os.remove(json_path)
            if video_path and os.path.exists(video_path):
                os.remove(video_path)
            if os.path.exists(thumb_path):
                os.remove(thumb_path)
    except Exception as e:
        print(f"[PlayerListActions] Error deleting record {json_filename}: {e}")


def prompt_and_rename_record(
    parent: QWidget, save_dir: str, json_filename: str, current_name: str
) -> bool:
    new_name, ok = QInputDialog.getText(parent, "Rename", "Enter new name:", text=current_name)
    if ok and new_name and new_name != current_name:
        json_path = os.path.join(save_dir, json_filename)
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["custom_name"] = new_name
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            print(f"[PlayerListActions] Error saving custom name: {e}")
    return False


def show_record_filter_menu(
    parent: QWidget,
    anchor_btn: QPushButton,
    t: object,
    available_agents: set[str],
    available_maps: set[str],
    on_select: Callable[[Optional[tuple[str, object]]], None],
) -> None:
    menu = QMenu(parent)

    action_clear = menu.addAction(t.filter_clear)
    action_clear.triggered.connect(lambda checked=False: on_select(None))
    menu.addSeparator()

    action_fav = menu.addAction(t.filter_favorite)
    action_fav.triggered.connect(lambda checked=False: on_select(("favorite", True)))

    menu_agent = menu.addMenu(t.filter_agent)
    for agent in sorted(available_agents):
        action = menu_agent.addAction(agent)
        action.triggered.connect(lambda checked, a=agent: on_select(("agent", a)))

    menu_result = menu.addMenu(t.filter_result)
    for res, label in [("win", t.result_win), ("loss", t.result_loss), ("draw", t.result_draw)]:
        action = menu_result.addAction(label)
        action.triggered.connect(lambda checked, r=res: on_select(("result", r)))

    menu_map = menu.addMenu(t.filter_map)
    for m in sorted(available_maps):
        action = menu_map.addAction(m)
        action.triggered.connect(lambda checked, m_name=m: on_select(("map", m_name)))

    menu.exec(anchor_btn.mapToGlobal(QPoint(0, anchor_btn.height())))


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


def _resolve_record_time_bounds(
    save_dir: str, json_filename: str, data: dict, match_info: dict
) -> tuple[str, float, float]:
    video_path = find_video_for_json(save_dir, json_filename, data)
    start_time = float(
        match_info.get("local_match_start_time")
        or data.get("local_match_start_time")
        or (
            (match_info.get("local_recording_start_time_ms") or data.get("local_recording_start_time_ms") or 0)
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
            (match_info.get("local_real_match_end_time_ms") or data.get("local_real_match_end_time_ms") or 0)
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


def _find_best_matching_api_match(
    matches: list[dict], start_time: float, end_time: float, existing_match_id: str
) -> Optional[dict]:
    if existing_match_id and not existing_match_id.startswith(("custom_", "pending_", "recovered_")):
        for m in matches:
            if str(m.get("metadata", {}).get("matchid") or "") == existing_match_id:
                return m

    best_match = None
    best_diff = float("inf")
    rec_end = max(start_time + 60.0, end_time)

    for m in matches:
        meta = m.get("metadata") or {}
        game_start = float(meta.get("game_start") or 0)
        if game_start <= 0:
            continue
        raw_len = float(meta.get("game_length") or 0)
        game_len_sec = raw_len / 1000.0 if raw_len > 10800 else raw_len
        game_end = game_start + max(game_len_sec, 300.0)

        diff = abs(game_start - start_time)
        overlaps = (start_time - 900.0) <= game_end and (rec_end + 600.0) >= game_start
        if (diff <= 2400.0 or (overlaps and diff <= 5400.0)) and diff < best_diff:
            best_diff = diff
            best_match = m

    return best_match


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
    custom_name = data.get("custom_name")
    is_favorite = data.get("is_favorite", False)
    video_path, start_time, end_time = _resolve_record_time_bounds(
        save_dir, json_filename, data, match_info
    )

    local_round_events = list(match_info.get("local_round_events") or [])
    local_ability_events = list(match_info.get("local_ability_events") or [])
    start_time_ms = match_info.get("local_recording_start_time_ms")
    real_match_end_time_ms = match_info.get("local_real_match_end_time_ms")

    # ローカルShooterGame.logから欠損しているイベント・マップ情報を補完
    log_extracted = _extract_events_from_local_logs(start_time, end_time)
    if not local_round_events and log_extracted["round_events"]:
        local_round_events = log_extracted["round_events"]
    if not local_ability_events and log_extracted["ability_events"]:
        local_ability_events = log_extracted["ability_events"]
    if real_match_end_time_ms is None and log_extracted["real_match_end_time_ms"] is not None:
        real_match_end_time_ms = log_extracted["real_match_end_time_ms"]

    riot_id = (getattr(config, "RIOT_ID", "") or "").strip()
    tag_line = (getattr(config, "TAG_LINE", "") or "").strip()
    region = (getattr(config, "REGION", "ap") or "ap").strip() or "ap"

    if not riot_id or not tag_line:
        try:
            from scripts.get_local_api_info import get_client_region, get_current_player

            det_name, det_tag = get_current_player()
            det_region = get_client_region()
            if det_name and det_tag:
                riot_id = det_name
                tag_line = det_tag
                region = det_region or region
                config.RIOT_ID = riot_id
                config.TAG_LINE = tag_line
                config.REGION = region
                config.save()
        except Exception:
            pass

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

    try:
        api = HenrikAPI(region, riot_id, tag_line)
        matches = api.fetch_matches(size=10, retries=2, delay=2)
        matched_data = _find_best_matching_api_match(matches, start_time, end_time, existing_match_id)
        if matched_data:
            match_id = matched_data["metadata"]["matchid"]
            mode = str(matched_data.get("metadata", {}).get("mode") or "")
            if mode.lower() not in NON_MMR_MODES:
                try:
                    mmr_change = api.fetch_mmr_change(match_id, retries=2, delay=2)
                except Exception:
                    pass
    except Exception as e:
        api_error_str = str(e)

    if matched_data:
        party_members = MatchMetadataService._extract_party_members(matched_data, riot_id, tag_line)
        matched_data["party_members"] = party_members
        matched_data["local_video_path"] = video_path
        matched_data["local_match_start_time"] = start_time
        matched_data["local_match_end_time"] = end_time
        matched_data["local_round_events"] = local_round_events
        matched_data["local_ability_events"] = local_ability_events
        matched_data["is_fetching_api"] = False
        if start_time_ms is not None:
            matched_data["local_recording_start_time_ms"] = start_time_ms
        if real_match_end_time_ms is not None:
            matched_data["local_real_match_end_time_ms"] = real_match_end_time_ms

        store = MetadataStore(config)
        new_filepath = store.save_match_metadata(matched_data, mmr_change)

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

        msg = "✅ 試合ログを再取得しました" if is_ja else "✅ Successfully refetched match log."
        return True, new_filename, msg

    # APIに該当試合がない場合でもローカルログからイベントやマップが補完できた場合は反映する
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

    if updated_local:
        msg = (
            "ローカルログからマップ・ラウンド情報を復元しました（APIには該当試合が見つかりませんでした）"
            if is_ja
            else "Restored map/round info from local logs (match not found on API)."
        )
        return True, json_filename, msg

    if api_error_str:
        err = f"API通信エラー ({api_error_str})" if is_ja else f"API error ({api_error_str})"
    else:
        err = (
            "APIの直近履歴に該当する試合データが見つかりませんでした"
            if is_ja
            else "Matching game data was not found in recent API history."
        )
    return False, json_filename, err


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


def show_record_sort_menu(
    parent: QWidget,
    anchor_btn: QPushButton,
    t: object,
    current_sort: str,
    on_select: Callable[[str], None],
) -> None:
    menu = QMenu(parent)
    sort_options = [
        ("date_desc", getattr(t, "sort_date_desc", "日付 (新しい順)")),
        ("date_asc", getattr(t, "sort_date_asc", "日付 (古い順)")),
        ("mmr_desc", getattr(t, "sort_mmr_desc", "MMR変動 (高い順)")),
        ("mmr_asc", getattr(t, "sort_mmr_asc", "MMR変動 (低い順)")),
    ]

    for sort_key, label in sort_options:
        action = menu.addAction(label)
        action.setCheckable(True)
        if current_sort == sort_key:
            action.setChecked(True)
        action.triggered.connect(lambda checked, s=sort_key: on_select(s))

    menu.exec(anchor_btn.mapToGlobal(QPoint(0, anchor_btn.height())))
