"""Module: @role: 録画プロセスの死活監視・クラッシュ復旧・未処理動画の定期ポーリングを担うスーパーバイザー"""
import os
import threading
import time
from typing import TYPE_CHECKING
from api.henrik_api import HenrikAPI

if TYPE_CHECKING:
    from ui.watcher_thread import WatcherThread


class WatcherSupervisor:
    def __init__(self, watcher_thread: "WatcherThread") -> None:
        self.wt = watcher_thread
        self._is_running = False
        self._bg_thread: threading.Thread = None

    def start(self) -> None:
        self._is_running = True
        self._bg_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._bg_thread.start()

    def stop(self) -> None:
        self._is_running = False

    def _run_loop(self) -> None:
        self.wt.metadata_service.cleanup_old_records(
            self.wt.current_video_path, self.wt._processing_video_paths
        )
        last_cleanup_time = time.time()
        last_api_check_time = time.time()

        while self._is_running:
            time.sleep(1)
            now = time.time()

            # --- 1. 録画プロセスの死活監視と自動再開 (クラッシュ対策) ---
            if self.wt.current_video_path is not None:
                if getattr(self.wt.watcher, "is_in_match", False) and not self.wt._is_manual_recording:
                    if self.wt._is_range_match():
                        self.wt.log_signal.emit(self.wt.t.log_range_transition_cancel)
                        self.wt.current_video_path = None
                        self.wt.recording_state_changed.emit(False)
                        if hasattr(self.wt.recorder, "cancel_recording"):
                            self.wt.recorder.cancel_recording()
                        else:
                            try:
                                self.wt.recorder.stop_recording()
                            except Exception:
                                pass
                        continue

                if not self.wt.recorder.is_recording():
                    if not self.wt._is_manual_recording and self.wt._is_range_match():
                        self.wt.current_video_path = None
                        self.wt.recording_state_changed.emit(False)
                        continue
                    was_manual = self.wt._is_manual_recording
                    self.wt.log_signal.emit(self.wt.t.log_ffmpeg_crashed.format(code=-1))
                    self.wt._stop_and_process_recording()
                    time.sleep(2)
                    if was_manual:
                        self.wt.start_manual_recording()
                    elif getattr(self.wt.watcher, "is_in_match", False) and not self.wt._is_range_match():
                        self.wt.handle_match_start(is_range=False, is_recovery=True)
                    continue

            # --- 2. 定期クリーンアップ (1時間ごと) ---
            if now - last_cleanup_time > 3600:
                self.wt.metadata_service.cleanup_old_records(
                    self.wt.current_video_path, self.wt._processing_video_paths
                )
                last_cleanup_time = now

            # --- 3. 未処理動画のAPIチェック (60秒ごと) ---
            if now - last_api_check_time > 60:
                last_api_check_time = now

                if getattr(self.wt.watcher, "is_in_match", False):
                    continue

                pending_videos = self.wt.metadata_service.get_pending_videos(
                    self.wt.current_video_path, self.wt._processing_video_paths
                )
                if not pending_videos:
                    continue

                self.wt.log_signal.emit(self.wt.t.log_bg_pending_videos.format(count=len(pending_videos)))

                try:
                    if not self.wt.current_riot_id or not self.wt.current_tag_line:
                        from scripts.get_local_api_info import get_client_region, get_current_player

                        name, tag = get_current_player()
                        region = get_client_region()
                        if name and tag:
                            self.wt.current_riot_id = name
                            self.wt.current_tag_line = tag
                            self.wt.current_region = region if region else self.wt.config.REGION

                            if (
                                self.wt.config.RIOT_ID != name
                                or self.wt.config.TAG_LINE != tag
                                or self.wt.config.REGION != self.wt.current_region
                            ):
                                self.wt.config.RIOT_ID = name
                                self.wt.config.TAG_LINE = tag
                                self.wt.config.REGION = self.wt.current_region
                                self.wt.config.save()

                            self.wt.log_signal.emit(
                                self.wt.t.log_bg_player_detected.format(
                                    name=name, tag=tag, region=self.wt.current_region
                                )
                            )
                        elif self.wt.config.RIOT_ID and self.wt.config.TAG_LINE:
                            self.wt.current_riot_id = self.wt.config.RIOT_ID
                            self.wt.current_tag_line = self.wt.config.TAG_LINE
                            self.wt.current_region = self.wt.config.REGION
                        else:
                            continue

                    self.wt.log_signal.emit(
                        self.wt.t.log_bg_fetching.format(
                            name=self.wt.current_riot_id,
                            tag=self.wt.current_tag_line,
                            region=self.wt.current_region,
                        )
                    )
                    api = HenrikAPI(
                        self.wt.current_region, self.wt.current_riot_id, self.wt.current_tag_line
                    )

                    try:
                        api_match_data = api.fetch_latest_match(retries=1, delay=2)
                    except Exception as e:
                        self.wt.log_signal.emit(self.wt.t.log_bg_api_error.format(error=e))
                        api_match_data = None

                    if not api_match_data:
                        for video_path, vid_time in pending_videos:
                            vid_mtime = (
                                os.path.getmtime(video_path)
                                if os.path.exists(video_path)
                                else vid_time
                            )
                            if time.time() - vid_mtime > 3600:
                                self.wt.log_signal.emit(
                                    self.wt.t.log_bg_fetch_failed_perm.format(
                                        file=os.path.basename(video_path)
                                    )
                                )
                                self.wt.metadata_service.create_dummy_metadata(
                                    video_path, vid_time, end_time=vid_mtime
                                )
                        continue

                    game_start = api_match_data.get("metadata", {}).get("game_start", 0)

                    for video_path, vid_time in pending_videos:
                        vid_mtime = (
                            os.path.getmtime(video_path) if os.path.exists(video_path) else vid_time
                        )
                        is_time_match = (
                            abs(game_start - vid_time) < 7200
                            or ((vid_time - 1800) <= game_start <= (vid_mtime + 600))
                        )

                        if is_time_match:
                            self.wt.log_signal.emit(
                                self.wt.t.log_bg_match_found.format(file=os.path.basename(video_path))
                            )
                            match_id = api_match_data["metadata"]["matchid"]
                            mode = api_match_data.get("metadata", {}).get("mode", "")
                            mmr_change = 0

                            if mode.lower() not in [
                                "deathmatch",
                                "custom game",
                                "escalation",
                                "snowball fight",
                                "replication",
                            ]:
                                try:
                                    mmr_change = api.fetch_mmr_change(match_id, retries=1, delay=2)
                                except Exception as e:
                                    self.wt.log_signal.emit(self.wt.t.log_bg_mmr_skipped.format(error=e))

                            api_match_data["local_video_path"] = video_path
                            filepath = self.wt.store.save_match_metadata(api_match_data, mmr_change)
                            self.wt.log_signal.emit(self.wt.t.log_bg_saved.format(path=filepath))
                            self.wt.match_saved_signal.emit()

                        elif game_start > vid_mtime + 3600 or time.time() - vid_mtime > 3600:
                            self.wt.log_signal.emit(
                                self.wt.t.log_bg_custom_or_unavailable.format(
                                    file=os.path.basename(video_path)
                                )
                            )
                            self.wt.metadata_service.create_dummy_metadata(
                                video_path, vid_time, end_time=vid_mtime
                            )

                except Exception as e:
                    self.wt.log_signal.emit(self.wt.t.log_bg_error.format(error=e))
