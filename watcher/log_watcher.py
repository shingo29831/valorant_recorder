import os
import time
import subprocess
import re
import datetime
from typing import Callable

import datetime

class LogWatcher:
    def __init__(self, on_match_start: Callable[[bool, float, bool], None], on_match_end: Callable[[bool], None], on_real_match_end: Callable[[], None], on_round_phase_changed: Callable[[str, float], None] = None, on_ability_used: Callable[[float], None] = None, on_performance_drop: Callable[[], None] = None):
        self.on_match_start = on_match_start
        self.on_match_end = on_match_end
        self.on_real_match_end = on_real_match_end
        self.on_round_phase_changed = on_round_phase_changed
        self.on_ability_used = on_ability_used
        self.on_performance_drop = on_performance_drop
        self.is_in_match = False
        self.is_match_ending = False
        self.has_entered_in_progress = False
        self.is_range = False
        self.current_map_name = "Unknown"
        self._stop_requested = False
        self._pending_match_start = None
        self._range_skip_notified = False
        self.map_name_pattern = re.compile(r"(?:Map Name:\s*|\[Map:\s*)(.*?)(?:\s*\||\s*\])")
        self.world_map_pattern = re.compile(r"/Game/Maps/([^/\s\.\'\"]+)(?:/([^/\s\.\'\"]+))?", re.IGNORECASE)
        self.range_keyword_pattern = re.compile(
            r"(?:\bthe\s+range\b|\brange(?:v\d+)?\b|poveglia|basictraining|shootingrange|shooting|tutorial)",
            re.IGNORECASE
        )
        self.state_transition_pattern = re.compile(r"(?:State:|Match State Changed from)\s*(\w+)\s*(?:->|to)\s*(\w+)")
        self.ignored_module_pattern = re.compile(
            r"\b(?:LogPlatformVoice|LogVoice|LogOnlineVoice|VoiceChat|Vivox|PartyVoice|TeamVoice|LogParty|LogChat|LogRMS|LogWebSocket|LogPresence|LogRichPresence|LogOnline|LogNet|LogHttp|LogAnalytics|LogTelemetry|Xmpp)\b",
            re.IGNORECASE
        )
        self.valid_game_states = {
            "WaitingToStart", "PreRound", "InProgress", "PostRound",
            "WaitingPostMatch", "LeavingMap", "PostGame",
            "Menus", "MainMenu", "Pregame", "InGame", "TransitionToMainMenu"
        }
        self.time_pattern = re.compile(r"^\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d{3})\]")

    def stop(self):
        self._stop_requested = True

    def is_range_detected(self) -> bool:
        return bool(self.is_range or self.range_keyword_pattern.search(self.current_map_name or ""))

    def _update_map_state_from_line(self, line: str) -> bool:
        """ログ行からマップ名および射撃訓練場フラグを更新し、実マップが更新された場合はTrueを返す"""
        updated_real_map = False
        if "LogMapLoadModel: Update:" in line:
            map_match = self.map_name_pattern.search(line)
            if map_match:
                extracted_map_name = map_match.group(1).strip()
                lower_map = extracted_map_name.lower()
                if lower_map in ["mainmenuv2", "mainmenu", "transitiontomainmenu"]:
                    self.current_map_name = "Unknown"
                    self.is_range = False
                    self._pending_match_start = None
                    self._range_skip_notified = False
                elif lower_map not in ["characterselect", "characterselectpersistentlevel"]:
                    # キャラ選択画面のロードで直前のRange判定が消えるのを防ぐため実マップ時のみ更新
                    self.current_map_name = extracted_map_name
                    self.is_range = bool(self.range_keyword_pattern.search(extracted_map_name) or self.range_keyword_pattern.search(line))
                    updated_real_map = True
            elif self.range_keyword_pattern.search(line):
                self.current_map_name = "Range"
                self.is_range = True
                updated_real_map = True

        elif any(k in line for k in ("LoadMap:", "Bringing World", "ServerTravel", "/Game/Maps/")):
            world_match = self.world_map_pattern.search(line)
            if world_match:
                folder_name = world_match.group(1) or ""
                sub_name = world_match.group(2) or folder_name
                lower_cand = f"{folder_name}/{sub_name}".lower()
                if any(m in lower_cand for m in ("mainmenu", "transitiontomainmenu")):
                    self.current_map_name = "Unknown"
                    self.is_range = False
                    self._pending_match_start = None
                    self._range_skip_notified = False
                elif "characterselect" not in lower_cand:
                    if self.range_keyword_pattern.search(lower_cand):
                        self.current_map_name = "Range"
                        self.is_range = True
                    else:
                        self.current_map_name = sub_name
                        self.is_range = False
                    updated_real_map = True
        return updated_real_map

    def _scan_recent_log_state(self, f) -> None:
        """監視開始時に既存ログの末尾を走査し、既に射撃場にいる場合の誤録画を防ぐ"""
        try:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            read_size = min(size, 512 * 1024)
            f.seek(max(0, size - read_size), os.SEEK_SET)
            lines = f.read().splitlines()
            for raw_line in lines:
                line = raw_line.strip()
                if not line:
                    continue
                self._update_map_state_from_line(line)
                if (
                    "Broadcasting state changed to Menus" in line or
                    "Broadcasting state changed to MainMenu" in line or
                    "Broadcasting state changed to TransitionToMainMenu" in line or
                    "Transitioning to State: Menus" in line or
                    "Loopstate changed from INGAME to MENUS" in line or
                    "Loopstate changed from PREGAME to MENUS" in line
                ):
                    self.current_map_name = "Unknown"
                    self.is_range = False
                    self._pending_match_start = None
                    self._range_skip_notified = False
        except Exception:
            pass
        finally:
            try:
                f.seek(0, os.SEEK_END)
            except Exception:
                pass

    def _flush_pending_match_start(self, force: bool = False) -> None:
        if self._pending_match_start is None:
            return
        log_time, is_recovery, queued_at = self._pending_match_start
        if self.is_range_detected():
            self._pending_match_start = None
            if not self._range_skip_notified:
                self._range_skip_notified = True
                self.on_match_start(True, log_time, is_recovery)
            return
        if self.current_map_name != "Unknown" or force or (time.time() - queued_at >= 1.5):
            self._pending_match_start = None
            self.on_match_start(False, log_time, is_recovery)

    def _parse_log_time(self, line: str) -> float:
        match = self.time_pattern.search(line)
        if match:
            time_str = match.group(1)
            try:
                dt = datetime.datetime.strptime(time_str, "%Y.%m.%d-%H.%M.%S:%f")
                return dt.timestamp()
            except ValueError:
                pass
        return time.time()

    def start_watching(self):
        import logging
        local_app_data = os.environ.get('LOCALAPPDATA')
        if not local_app_data:
            user_profile = os.environ.get('USERPROFILE')
            if user_profile:
                local_app_data = os.path.join(user_profile, 'AppData', 'Local')
            else:
                logging.error("LOCALAPPDATA and USERPROFILE environment variables not found.")
                raise EnvironmentError("LOCALAPPDATA environment variable not found.")

        log_path = os.path.join(local_app_data, 'VALORANT', 'Saved', 'Logs', 'ShooterGame.log')
        self._stop_requested = False

        # Valorant起動前にアプリが起動された場合もエラー終了せず生成を待機する
        while not os.path.exists(log_path):
            if self._stop_requested:
                return
            time.sleep(2.0)

        logging.info(f"Started watching log file: {log_path}")

        try:
            f = open(log_path, 'r', encoding='utf-8', errors='replace')
            self._scan_recent_log_state(f)
            
            try:
                while not self._stop_requested:
                    line = f.readline()
                    if not line:
                        self._flush_pending_match_start()
                        # EOF到達時のみファイルサイズ縮小(Valorant再起動)をチェックしI/Oコールを最小化
                        try:
                            current_size = os.path.getsize(log_path)
                            if current_size < f.tell():
                                logging.info("Log file was cleared (Valorant restarted). Reopening...")
                                f.close()
                                f = open(log_path, 'r', encoding='utf-8', errors='replace')
                                f.seek(0, os.SEEK_END)
                                self.is_in_match = False
                                self.has_entered_in_progress = False
                                self.current_map_name = "Unknown"
                                self.is_range = False
                                self._pending_match_start = None
                                self._range_skip_notified = False
                                continue
                        except OSError:
                            pass
                        time.sleep(0.15)
                        continue
                    
                    line = line.strip()
                    if not line:
                        continue

                    if self._update_map_state_from_line(line):
                        self._flush_pending_match_start()

                    # 無関係なログ行での正規表現・時刻パース処理をスキップしてCPU負荷を削減
                    if not any(k in line for k in ("State:", "Match State", "Broadcasting state", "Loopstate", "LogAbilitySystem:", "LogShooterGame: Match ended", "Warning:")):
                        continue

                    # ボイスや通信・パーティー等別モジュールの状態遷移ログによる誤検知を排除
                    if self.ignored_module_pattern.search(line):
                        continue

                    log_time = self._parse_log_time(line)

                    # 状態遷移パターンの検索（ボイス等のConnected/Disconnected遷移を除外しゲーム状態のみ抽出）
                    transition_match = self.state_transition_pattern.search(line)
                    to_state = None
                    from_state = None
                    if transition_match:
                        cand_from = transition_match.group(1)
                        cand_to = transition_match.group(2)
                        if cand_to in self.valid_game_states or cand_from in self.valid_game_states:
                            from_state = cand_from
                            to_state = cand_to

                    # メニューに戻った検知（試合終了フェーズのリセット用）
                    is_menu = (
                        "Broadcasting state changed to Menus" in line or
                        "Broadcasting state changed to MainMenu" in line or
                        "Broadcasting state changed to TransitionToMainMenu" in line or
                        "Transitioning to State: Menus" in line or
                        "Loopstate changed from INGAME to MENUS" in line or
                        "Loopstate changed from PREGAME to MENUS" in line or
                        (to_state == "Menus")
                    )

                    # アビリティ使用検知
                    is_ability = "LogAbilitySystem:" in line and "Ability activated" in line
                    
                    # 試合中（途中復帰用）およびラウンドフェーズ検知
                    is_progress = False
                    round_phase = None
                    
                    if to_state in ["PreRound", "InProgress", "PostRound"]:
                        is_progress = True
                        round_phase = to_state
                        if to_state in ["InProgress", "PostRound"]:
                            self.has_entered_in_progress = True
                    elif is_ability:
                        is_progress = True
                        self.has_entered_in_progress = True
                    elif "Broadcasting state changed to InGame" in line or "Loopstate changed from PREGAME to INGAME" in line:
                        # キャラ選択から実マップへのロード遷移ではまだラウンド進行済みフラグを立てずロード直後の誤終了を防ぐ
                        is_progress = True

                    if is_menu:
                        self.is_match_ending = False
                        self._pending_match_start = None
                        self._range_skip_notified = False
                        # メニュー帰還時に前回マップ名が残って次回の射撃場で誤判定するのを防ぐ
                        if not self.is_in_match:
                            self.current_map_name = "Unknown"
                            self.is_range = False

                    # 試合開始の検知 (ピック画面から完全録画するため Pregame を追加)
                    is_start = (
                        "Broadcasting state changed to Pregame" in line or
                        "Loopstate changed from MENUS to PREGAME" in line or
                        "Broadcasting state changed to InGame" in line or
                        "Loopstate changed from MENUS to INGAME" in line or
                        (to_state in ["PreRound", "InProgress"] and from_state == "WaitingToStart")
                    )
                    
                    # 試合終了の検知（[Map Complete: TRUE]やLeavingMapはマップロード/キャラ選択終了時にも出るため除外）
                    is_end_log = (
                        "Broadcasting state changed to PostGame" in line or
                        "LogShooterGame: Match ended" in line or
                        "Transitioning to State: PostGame" in line or
                        "Transitioning from InGame to TransitionToMainMenu" in line or
                        (to_state in ["WaitingPostMatch", "PostGame"])
                    )
                    
                    is_end = False
                    if self.is_in_match:
                        if is_end_log and self.has_entered_in_progress:
                            # 実際に試合が始まってから終了ログが出た場合のみ終了とみなす
                            is_end = True
                        elif is_menu:
                            # メニューに戻った場合は、進行状況に関わらず終了(またはドッジ/強制終了)とみなす
                            is_end = True

                    if is_start:
                        self.is_match_ending = False
                        if not self.is_in_match:
                            self.is_in_match = True
                            self.has_entered_in_progress = False
                            if self.is_range_detected():
                                self._pending_match_start = None
                                if not self._range_skip_notified:
                                    self._range_skip_notified = True
                                    self.on_match_start(True, log_time, False)
                            elif self.current_map_name == "Unknown":
                                # マップ名ログが直後の行に出力されるケースに備えて開始判定を保留
                                self._pending_match_start = (log_time, False, time.time())
                            else:
                                self._pending_match_start = None
                                self.on_match_start(False, log_time, False)
                    elif is_progress and not self.is_in_match and not self.is_match_ending:
                        # 録画中断からの自動復帰 (試合終了直後ではない場合のみ)
                        self.is_in_match = True
                        self.has_entered_in_progress = True
                        if self.is_range_detected():
                            self._pending_match_start = None
                            if not self._range_skip_notified:
                                self._range_skip_notified = True
                                self.on_match_start(True, log_time, True)
                        elif self.current_map_name == "Unknown":
                            self._pending_match_start = (log_time, True, time.time())
                        else:
                            self._pending_match_start = None
                            self.on_match_start(False, log_time, True)
                    elif is_end and self.is_in_match:
                        was_range = self.is_range_detected()
                        self.is_in_match = False
                        self.is_match_ending = True
                        self.has_entered_in_progress = False
                        self._pending_match_start = None
                        self._range_skip_notified = False
                        self.on_match_end(was_range)
                        if is_menu:
                            self.current_map_name = "Unknown"
                            self.is_range = False
                        
                    if self._pending_match_start is not None:
                        self._flush_pending_match_start()

                    # ラウンドフェーズの記録
                    if self.is_in_match and round_phase and self.on_round_phase_changed:
                        self.on_round_phase_changed(round_phase, log_time)

                    # ウルト発動マーカー (LogAbilitySystem)
                    if self.is_in_match and self.on_ability_used and is_ability:
                        self.on_ability_used(log_time)

                    # 自動負荷コントロール (パフォーマンス低下の検知)
                    if self.is_in_match and self.on_performance_drop:
                        if "LogMemory: Warning:" in line or "LogRenderer: Warning:" in line or "LogLoadTimeMetrics: Warning:" in line:
                            self.on_performance_drop()
            finally:
                f.close()
                            
        except PermissionError as e:
            import logging
            logging.error(f"PermissionError: Access denied to log file. {e}")
            raise PermissionError("Access denied. The log file might be locked by Vanguard.")
        except Exception as e:
            import logging
            logging.error(f"Unexpected error in log watcher: {e}")
            raise