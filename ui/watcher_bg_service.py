"""Module: @role: 録画完了後のAPIメタデータ取得・未処理動画リカバリ・古い録画の自動削除を担うモジュール"""
from datetime import datetime
import json
import os
import re
import time
from typing import Callable, Optional, Set, Tuple
from api.henrik_api import HenrikAPI
from core.config import Config
from storage.metadata_store import MetadataStore

NON_MMR_MODES = ("deathmatch", "custom game", "escalation", "snowball fight", "replication")
RECORD_DATE_PATTERN = re.compile(r"(\d{8}_\d{6})")


class MatchMetadataService:
    def __init__(
        self,
        config: Config,
        store: MetadataStore,
        t: object,
        emit_log: Callable[[str], None],
        emit_match_saved: Callable[[], None],
    ):
        self.config = config
        self.store = store
        self.t = t
        self._emit_log = emit_log
        self._emit_match_saved = emit_match_saved

    def save_temp_metadata(
        self,
        video_path: str,
        map_name: str,
        start_time: float,
        end_time: float,
        events: list,
        ability_events: list,
        start_time_ms: Optional[int],
        real_match_end_time_ms: Optional[int],
    ) -> str:
        resolved_map = "Fetching..." if map_name == "Unknown" else map_name
        temp_match_data = {
            "metadata": {
                "matchid": f"pending_{int(start_time)}",
                "map": resolved_map,
                "game_start": int(start_time),
                "game_length": int(end_time - start_time) if end_time > start_time else 0,
                "mode": "Unknown",
            },
            "players": {"all_players": []},
            "kills": [],
            "rounds": [],
            "local_video_path": video_path,
            "local_match_start_time": start_time,
            "local_match_end_time": end_time,
            "local_round_events": events,
            "local_ability_events": ability_events,
            "is_fetching_api": True,
        }
        if start_time_ms is not None:
            temp_match_data["local_recording_start_time_ms"] = start_time_ms
        if real_match_end_time_ms is not None:
            temp_match_data["local_real_match_end_time_ms"] = real_match_end_time_ms

        temp_filepath = self.store.save_match_metadata(temp_match_data, 0)
        self._emit_match_saved()
        return temp_filepath

    def create_dummy_metadata(
        self,
        video_path: str,
        vid_time: float,
        map_name: str = "Custom / Unknown",
        end_time: float = 0,
        events: Optional[list] = None,
        temp_filepath: Optional[str] = None,
        start_time_ms: Optional[int] = None,
        real_match_end_time_ms: Optional[int] = None,
    ) -> None:
        if events is None:
            events = []
        resolved_map = "Custom / Unknown" if map_name == "Unknown" else map_name

        match_data = {
            "metadata": {
                "matchid": f"custom_{int(vid_time)}",
                "map": resolved_map,
                "game_start": int(vid_time),
                "game_length": int(end_time - vid_time) if end_time > vid_time else 0,
                "mode": "Custom",
            },
            "players": {"all_players": []},
            "kills": [],
            "rounds": [],
            "local_video_path": video_path,
            "local_match_start_time": vid_time,
            "local_match_end_time": end_time,
            "local_round_events": events,
            "local_ability_events": [],
            "is_fetching_api": False,
        }
        if start_time_ms is not None:
            match_data["local_recording_start_time_ms"] = start_time_ms
        if real_match_end_time_ms is not None:
            match_data["local_real_match_end_time_ms"] = real_match_end_time_ms

        filepath = self.store.save_match_metadata(match_data, 0)
        if temp_filepath and os.path.exists(temp_filepath) and temp_filepath != filepath:
            try:
                os.remove(temp_filepath)
            except OSError:
                pass
        self._emit_match_saved()

    def fetch_api_and_save(
        self,
        video_path: str,
        start_time: float,
        end_time: float,
        events: list,
        riot_id: Optional[str],
        tag_line: Optional[str],
        region: str,
        map_name: str,
        ability_events: Optional[list] = None,
        temp_filepath: Optional[str] = None,
        start_time_ms: Optional[int] = None,
        real_match_end_time_ms: Optional[int] = None,
    ) -> None:
        if ability_events is None:
            ability_events = []
        self._emit_log(self.t.log_api_checking)

        if not riot_id or not tag_line:
            self._emit_log(self.t.log_api_no_player_id)
            self.create_dummy_metadata(
                video_path, start_time, map_name, end_time, events, temp_filepath, start_time_ms, real_match_end_time_ms
            )
            return

        self._emit_log(self.t.log_api_fetching.format(name=riot_id, tag=tag_line, region=region))
        api = HenrikAPI(region, riot_id, tag_line)

        match_data = None
        mmr_change = 0

        for attempt in range(3):
            time.sleep(20)
            try:
                api_match_data = api.fetch_latest_match()
                game_start = api_match_data.get("metadata", {}).get("game_start", 0)
                is_time_match = (
                    abs(game_start - start_time) < 7200
                    or (end_time > start_time and (start_time - 1800) <= game_start <= (end_time + 600))
                )
                if is_time_match:
                    match_data = api_match_data
                    match_id = match_data["metadata"]["matchid"]
                    mode = match_data.get("metadata", {}).get("mode", "")
                    mmr_change = 0
                    if mode.lower() not in NON_MMR_MODES:
                        try:
                            mmr_change = api.fetch_mmr_change(match_id)
                        except Exception as e:
                            self._emit_log(self.t.log_api_mmr_skipped.format(error=e))
                    self._emit_log(self.t.log_api_fetch_success)
                    break
            except Exception as e:
                self._emit_log(self.t.log_api_fetch_error.format(attempt=attempt + 1, error=e))

        if match_data:
            try:
                party_members = self._extract_party_members(match_data, riot_id, tag_line)
                match_data["party_members"] = party_members
                match_data["local_video_path"] = video_path
                match_data["local_match_start_time"] = start_time
                match_data["local_match_end_time"] = end_time
                match_data["local_round_events"] = events
                match_data["local_ability_events"] = ability_events
                match_data["is_fetching_api"] = False
                if start_time_ms is not None:
                    match_data["local_recording_start_time_ms"] = start_time_ms
                if real_match_end_time_ms is not None:
                    match_data["local_real_match_end_time_ms"] = real_match_end_time_ms

                filepath = self.store.save_match_metadata(match_data, mmr_change)
                if temp_filepath and os.path.exists(temp_filepath) and temp_filepath != filepath:
                    try:
                        os.remove(temp_filepath)
                    except OSError:
                        pass
                self._emit_log(self.t.log_storage_saved.format(path=filepath))
                self._emit_match_saved()
            except Exception as e:
                self._emit_log(self.t.log_process_metadata_failed.format(error=e))
        else:
            self._emit_log(self.t.log_api_not_found)
            self.create_dummy_metadata(
                video_path, start_time, map_name, end_time, events, temp_filepath, start_time_ms, real_match_end_time_ms
            )

    @staticmethod
    def _extract_party_members(match_data: dict, riot_id: str, tag_line: str) -> list[str]:
        party_members = []
        target_party_id = None
        players = match_data.get("players", {}).get("all_players", [])
        for p in players:
            if p.get("name", "").lower() == riot_id.lower() and p.get("tag", "").lower() == tag_line.lower():
                target_party_id = p.get("party_id")
                break
        if target_party_id:
            for p in players:
                if p.get("party_id") == target_party_id:
                    party_members.append(f"{p.get('name')}#{p.get('tag')}")
        return party_members

    def get_pending_videos(
        self,
        current_video_path: Optional[str],
        processing_video_paths: Set[str],
    ) -> list[Tuple[str, float]]:
        if not os.path.exists(self.config.SAVE_DIR):
            return []

        videos = []
        jsons = []
        for f in os.listdir(self.config.SAVE_DIR):
            if f.endswith(".mp4"):
                videos.append(f)
            elif f.endswith(".json"):
                jsons.append(f)

        handled_videos = set()
        for jf in jsons:
            try:
                with open(os.path.join(self.config.SAVE_DIR, jf), "r", encoding="utf-8") as f:
                    data = json.load(f)
                    match_info = data.get("match_info", data)
                    vpath = match_info.get("local_video_path", "")
                    if vpath:
                        handled_videos.add(os.path.basename(vpath.replace("\\", "/")))
            except (OSError, json.JSONDecodeError):
                pass

        pending = []
        active_name = os.path.basename(current_video_path) if current_video_path else None
        for v in videos:
            if v == active_name or v in processing_video_paths or v in handled_videos:
                continue
            v_path = os.path.join(self.config.SAVE_DIR, v)
            try:
                if os.path.getsize(v_path) == 0:
                    os.remove(v_path)
                    continue
                if time.time() - os.path.getmtime(v_path) < 60:
                    continue
            except OSError:
                continue

            match = RECORD_DATE_PATTERN.search(v)
            if match:
                try:
                    dt = datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
                    pending.append((v_path, dt.timestamp()))
                except ValueError:
                    pass
        return pending

    def cleanup_old_records(
        self,
        current_video_path: Optional[str],
        processing_video_paths: Set[str],
    ) -> None:
        if self.config.AUTO_DELETE_DAYS <= 0:
            return
        save_dir = self.config.SAVE_DIR
        if not os.path.exists(save_dir):
            return

        now = time.time()
        cutoff = now - (self.config.AUTO_DELETE_DAYS * 86400)
        bases_to_delete = set()
        bases_to_keep = set()

        for f in os.listdir(save_dir):
            if not f.endswith(".json"):
                continue
            filepath = os.path.join(save_dir, f)
            try:
                with open(filepath, "r", encoding="utf-8") as jf:
                    data = json.load(jf)

                video_path = data.get("match_info", data).get("local_video_path", "")
                v_base = os.path.basename(video_path) if video_path else ""
                is_active = (video_path and video_path == current_video_path) or (v_base in processing_video_paths)
                is_broken = False
                if not is_active and (not video_path or not os.path.exists(video_path) or os.path.getsize(video_path) == 0):
                    if time.time() - os.path.getmtime(filepath) > 300:
                        is_broken = True

                if is_broken:
                    bases_to_delete.add(f[:-5])
                elif data.get("is_favorite", False):
                    bases_to_keep.add(f[:-5])
                elif os.path.getmtime(filepath) < cutoff:
                    bases_to_delete.add(f[:-5])
            except Exception:
                try:
                    if os.path.getmtime(filepath) < cutoff:
                        bases_to_delete.add(f[:-5])
                except OSError:
                    pass

        for f in os.listdir(save_dir):
            if f.endswith((".mp4", ".jpg")):
                base = os.path.splitext(f)[0]
                if base not in bases_to_keep and base not in bases_to_delete:
                    filepath = os.path.join(save_dir, f)
                    try:
                        if os.path.getmtime(filepath) < cutoff:
                            bases_to_delete.add(base)
                    except OSError:
                        pass

        deleted_count = 0
        for base in bases_to_delete:
            for ext in (".json", ".mp4", ".jpg"):
                filepath = os.path.join(save_dir, base + ext)
                if os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                    except Exception as e:
                        self._emit_log(self.t.log_bg_delete_failed.format(path=filepath, error=e))

        if deleted_count > 0:
            self._emit_log(self.t.log_bg_auto_deleted.format(count=deleted_count))
            self._emit_match_saved()
