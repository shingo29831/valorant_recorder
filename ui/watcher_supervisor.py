"""Module: @role: 録画プロセスの死活監視・クラッシュ復旧・未処理動画の定期ポーリングを担うスーパーバイザー"""
import os
import threading
import time
from typing import TYPE_CHECKING
from api.henrik_api import HenrikAPI
from ui.watcher_bg_service import NON_MMR_MODES, MatchMetadataService

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
                    err_detail = self.wt.recorder.get_last_error() if hasattr(self.wt.recorder, "get_last_error") else "-1"
                    self.wt.log_signal.emit(self.wt.t.log_ffmpeg_crashed.format(code=err_detail or -1))
                    # 異常終了したセッションを確実に停止・Remux完了させてから再開しプロセス競合を防止
                    crashed_video = self.wt.current_video_path
                    try:
                        self.wt.recorder.stop_recording(target_filepath=crashed_video)
                    except Exception:
                        pass
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
                    # アカウント切替に追従するため毎回ローカルクライアントの現在アカウントを確認する
                    from scripts.get_local_api_info import get_client_region, get_current_player

                    det_name, det_tag = get_current_player()
                    det_region = get_client_region()
                    if det_name and det_tag:
                        resolved_region = det_region if det_region else (self.wt.config.REGION or "ap")
                        account_changed = (
                            self.wt.current_riot_id != det_name
                            or self.wt.current_tag_line != det_tag
                            or self.wt.current_region != resolved_region
                        )
                        self.wt.current_riot_id = det_name
                        self.wt.current_tag_line = det_tag
                        self.wt.current_region = resolved_region

                        cfg_changed = False
                        if hasattr(self.wt.config, "register_known_account") and self.wt.config.register_known_account(det_name, det_tag):
                            cfg_changed = True
                        if (
                            self.wt.config.RIOT_ID != det_name
                            or self.wt.config.TAG_LINE != det_tag
                            or self.wt.config.REGION != resolved_region
                        ):
                            self.wt.config.RIOT_ID = det_name
                            self.wt.config.TAG_LINE = det_tag
                            self.wt.config.REGION = resolved_region
                            cfg_changed = True
                        if cfg_changed:
                            self.wt.config.save()
                        if account_changed:
                            self.wt.log_signal.emit(
                                self.wt.t.log_bg_player_detected.format(
                                    name=det_name, tag=det_tag, region=resolved_region
                                )
                            )
                    elif self.wt.config.RIOT_ID and self.wt.config.TAG_LINE:
                        self.wt.current_riot_id = self.wt.config.RIOT_ID.strip()
                        self.wt.current_tag_line = self.wt.config.TAG_LINE.strip()
                        self.wt.current_region = det_region or self.wt.config.REGION or "ap"
                    else:
                        for video_path, vid_time in pending_videos:
                            vid_mtime = (
                                os.path.getmtime(video_path)
                                if os.path.exists(video_path)
                                else vid_time
                            )
                            if time.time() - vid_mtime > 3600:
                                self.wt.metadata_service.create_dummy_metadata(
                                    video_path, vid_time, end_time=vid_mtime
                                )
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
                        matches = api.fetch_matches(size=10, retries=1, delay=2)
                    except Exception as e:
                        self.wt.log_signal.emit(self.wt.t.log_bg_api_error.format(error=e))
                        matches = []

                    if not matches:
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

                    latest_game_start = max(
                        (float(m.get("metadata", {}).get("game_start") or 0) for m in matches),
                        default=0.0,
                    )

                    for video_path, vid_time in pending_videos:
                        vid_mtime = (
                            os.path.getmtime(video_path) if os.path.exists(video_path) else vid_time
                        )
                        best_match = None
                        best_diff = float("inf")
                        for m in matches:
                            if not MatchMetadataService.is_player_in_match(
                                m, self.wt.current_riot_id, self.wt.current_tag_line
                            ):
                                continue
                            meta = m.get("metadata") or {}
                            game_start = float(meta.get("game_start") or 0)
                            if game_start <= 0:
                                continue
                            raw_len = float(meta.get("game_length") or 0)
                            game_len_sec = raw_len / 1000.0 if raw_len > 10800 else raw_len
                            game_end = game_start + max(game_len_sec, 60.0)
                            if game_end < (vid_time - 120.0):
                                continue
                            diff = abs(game_start - vid_time)
                            overlaps = (vid_time - 900.0) <= game_end and (vid_mtime + 600.0) >= game_start
                            if (diff <= 2400.0 or (overlaps and diff <= 5400.0)) and diff < best_diff:
                                best_diff = diff
                                best_match = m

                        if best_match is not None:
                            self.wt.log_signal.emit(
                                self.wt.t.log_bg_match_found.format(file=os.path.basename(video_path))
                            )
                            match_copy = dict(best_match)
                            match_id = match_copy["metadata"]["matchid"]
                            mode = match_copy.get("metadata", {}).get("mode", "")
                            mmr_change = 0

                            if mode.lower() not in NON_MMR_MODES:
                                try:
                                    mmr_change = api.fetch_mmr_change(match_id, retries=1, delay=2)
                                except Exception as e:
                                    self.wt.log_signal.emit(self.wt.t.log_bg_mmr_skipped.format(error=e))

                            rec_player = MatchMetadataService.build_recorded_player_info(
                                match_copy,
                                self.wt.current_riot_id,
                                self.wt.current_tag_line,
                                self.wt.current_region,
                            )
                            if rec_player:
                                match_copy["recorded_player"] = rec_player
                            match_copy["party_members"] = MatchMetadataService._extract_party_members(
                                match_copy, self.wt.current_riot_id, self.wt.current_tag_line
                            )
                            match_copy["local_video_path"] = video_path
                            match_copy["local_match_start_time"] = vid_time
                            match_copy["local_match_end_time"] = vid_mtime
                            match_copy["is_fetching_api"] = False
                            filepath = self.wt.store.save_match_metadata(match_copy, mmr_change)
                            self.wt.log_signal.emit(self.wt.t.log_bg_saved.format(path=filepath))
                            self.wt.match_saved_signal.emit()

                        elif latest_game_start > vid_mtime + 3600 or time.time() - vid_mtime > 3600:
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
