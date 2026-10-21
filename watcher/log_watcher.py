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
        self.map_name_pattern = re.compile(r"(?:Map Name:\s*|\[Map:\s*)(.*?)(?:\s*\||\s*\])")
        self.state_transition_pattern = re.compile(r"(?:State:|Match State Changed from)\s*(\w+)\s*(?:->|to)\s*(\w+)")
        self.time_pattern = re.compile(r"^\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d{3})\]")

    def stop(self):
        self._stop_requested = True

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
            f.seek(0, os.SEEK_END)
            
            try:
                while not self._stop_requested:
                    line = f.readline()
                    if not line:
                        # EOF到達時のみファイルサイズ縮小(Valorant再起動)をチェックしI/Oコールを最小化
                        try:
                            current_size = os.path.getsize(log_path)
                            if current_size < f.tell():
                                logging.info("Log file was cleared (Valorant restarted). Reopening...")
                                f.close()
                                f = open(log_path, 'r', encoding='utf-8', errors='replace')
                                f.seek(0, os.SEEK_END)
                                continue
                        except OSError:
                            pass
                        time.sleep(0.15)
                        continue
                    
                    line = line.strip()
                    if not line:
                        continue

                    if "LogMapLoadModel: Update:" in line:
                        map_match = self.map_name_pattern.search(line)
                        if map_match:
                            extracted_map_name = map_match.group(1).strip()
                            if extracted_map_name.lower() not in ["mainmenuv2", "mainmenu", "characterselect", "characterselectpersistentlevel"]:
                                self.current_map_name = extracted_map_name
                            
                        line_lower = line.lower()
                        if any(x in line_lower for x in ["range", "rangev2", "poveglia", "basictraining", "shooting", "tutorial"]):
                            self.is_range = True
                        else:
                            self.is_range = False

                    # 無関係なログ行での正規表現・時刻パース処理をスキップしてCPU負荷を削減
                    if not any(k in line for k in ("State:", "Match State", "Broadcasting state", "Loopstate", "LogAbilitySystem:", "LogShooterGame: Match ended", "[Map Complete:", "Leaving session", "Warning:")):
                        continue

                    log_time = self._parse_log_time(line)

                    # 状態遷移パターンの検索
                    transition_match = self.state_transition_pattern.search(line)
                    to_state = None
                    from_state = None
                    if transition_match:
                        from_state = transition_match.group(1)
                        to_state = transition_match.group(2)

                    # メニューに戻った検知（試合終了フェーズのリセット用）
                    is_menu = (
                        "Broadcasting state changed to Menus" in line or
                        "Broadcasting state changed to MainMenu" in line or
                        "Broadcasting state changed to TransitionToMainMenu" in line or
                        "Transitioning to State: Menus" in line or
                        "Leaving session" in line or
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
                        if to_state == "InProgress":
                            self.has_entered_in_progress = True
                    elif is_ability:
                        is_progress = True
                        self.has_entered_in_progress = True
                    elif "Broadcasting state changed to InGame" in line or "Loopstate changed from PREGAME to INGAME" in line:
                        is_progress = True
                        self.has_entered_in_progress = True

                    if is_menu:
                        self.is_match_ending = False
                        # メニューに戻った場合は確実に試合中フラグを初期化
                        self.is_in_match = False
                        self.has_entered_in_progress = False

                    # 試合開始の検知 (ピック画面から完全録画するため Pregame を追加)
                    is_start = (
                        "Broadcasting state changed to Pregame" in line or
                        "Loopstate changed from MENUS to PREGAME" in line or
                        "Broadcasting state changed to InGame" in line or
                        "Loopstate changed from MENUS to INGAME" in line or
                        (to_state in ["PreRound", "InProgress"] and from_state == "WaitingToStart")
                    )
                    
                    # 試合終了の検知
                    is_end_log = (
                        "Broadcasting state changed to PostGame" in line or
                        "LogShooterGame: Match ended" in line or
                        "Transitioning to State: PostGame" in line or
                        "Transitioning from InGame to TransitionToMainMenu" in line or
                        "[Map Complete: TRUE | Changed: TRUE]" in line or
                        (to_state in ["WaitingPostMatch", "LeavingMap", "Disconnected", "PostGame"])
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
                            self.on_match_start(self.is_range, log_time, False)
                    elif is_progress and not self.is_in_match and not self.is_match_ending:
                        # 録画中断からの自動復帰 (試合終了直後ではない場合のみ)
                        self.is_in_match = True
                        self.has_entered_in_progress = True
                        self.on_match_start(self.is_range, log_time, True)
                    elif is_end and self.is_in_match:
                        self.is_in_match = False
                        self.is_match_ending = True
                        self.has_entered_in_progress = False
                        self.on_match_end(self.is_range)
                        
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