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
        self.is_range = False
        self.phase_pattern_1 = re.compile(r"State:\s*\w+\s*->\s*(PreRound|InProgress|PostRound)")
        self.phase_pattern_2 = re.compile(r"Match State Changed from\s*\w+\s*to\s*(PreRound|InProgress|PostRound)")
        self.time_pattern = re.compile(r"^\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d{3})\]")

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
        local_app_data = os.environ.get('LOCALAPPDATA')
        if not local_app_data:
            raise EnvironmentError("LOCALAPPDATA environment variable not found.")

        log_path = os.path.join(local_app_data, 'VALORANT', 'Saved', 'Logs', 'ShooterGame.log')
        
        if not os.path.exists(log_path):
            raise FileNotFoundError(f"Log file not found: {log_path}")

        try:
            f = open(log_path, 'r', encoding='utf-8', errors='replace')
            f.seek(0, os.SEEK_END)
            
            try:
                while True:
                    try:
                        current_size = os.path.getsize(log_path)
                        if current_size < f.tell():
                            # ファイルがクリアされた(Valorant再起動)場合は開き直す
                            f.close()
                            f = open(log_path, 'r', encoding='utf-8', errors='replace')
                            f.seek(0, os.SEEK_END)
                            continue
                    except OSError:
                        pass

                    line = f.readline()
                    if not line:
                        time.sleep(0.1)
                        continue
                    
                    line = line.strip()
                    if not line:
                        continue

                    # マップロード時に射撃訓練場(Range)かどうかを判定
                    if "LogMapLoadModel: Update:" in line and "Map Name:" in line:
                        if "Range" in line or "Poveglia" in line:
                            self.is_range = True
                        else:
                            self.is_range = False

                    log_time = self._parse_log_time(line)

                    # 試合開始の検知 (ピック画面から完全録画するため Pregame を追加)
                    is_start = (
                        "LogPregameManager:" in line or
                        "Broadcasting state changed to Pregame" in line or
                        "Match State Changed from WaitingToStart to PreRound" in line or
                        "Match State Changed from WaitingToStart to InProgress" in line or
                        "State: WaitingToStart -> PreRound" in line or
                        "State: WaitingToStart -> InProgress" in line or
                        "Broadcasting state changed to InGame" in line
                    )
                    
                    # 試合中（途中復帰用）およびラウンドフェーズ検知
                    is_progress = False
                    match_phase = self.phase_pattern_1.search(line) or self.phase_pattern_2.search(line)
                    if match_phase:
                        is_progress = True
                    elif "Broadcasting state changed to InGame" in line:
                        is_progress = True

                    # 試合終了の検知
                    is_end = (
                        "Match State Changed from InProgress to WaitingPostMatch" in line or
                        "State: InProgress -> WaitingPostMatch" in line or
                        "Broadcasting state changed to PostGame" in line or
                        "Broadcasting state changed to TransitionToMainMenu" in line
                    )

                    was_in_match = self.is_in_match

                    if is_start and not self.is_in_match:
                        self.is_in_match = True
                        self.on_match_start(self.is_range, log_time, False)
                    elif is_progress and not self.is_in_match:
                        # 録画中断からの自動復帰
                        self.is_in_match = True
                        self.on_match_start(self.is_range, log_time, True)
                    elif is_end and self.is_in_match:
                        self.is_in_match = False
                        self.on_match_end(self.is_range)
                        
                    # ラウンドフェーズの記録
                    if self.is_in_match and match_phase and self.on_round_phase_changed:
                        self.on_round_phase_changed(match_phase.group(1), log_time)

                    # ウルト発動マーカー (LogAbilitySystem)
                    if self.is_in_match and self.on_ability_used and "LogAbilitySystem:" in line and "Ability activated" in line:
                        self.on_ability_used(log_time)

                    # 自動負荷コントロール (パフォーマンス低下の検知)
                    if self.is_in_match and self.on_performance_drop:
                        if "LogMemory: Warning:" in line or "LogRenderer: Warning:" in line or "LogLoadTimeMetrics: Warning:" in line:
                            self.on_performance_drop()
            finally:
                f.close()
                            
        except PermissionError:
            raise PermissionError("Access denied. The log file might be locked by Vanguard.")