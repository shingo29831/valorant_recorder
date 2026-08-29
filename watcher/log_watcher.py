import os
import time
import subprocess
import re
from typing import Callable

class LogWatcher:
    def __init__(self, on_match_start: Callable[[bool], None], on_match_end: Callable[[bool], None], on_real_match_end: Callable[[], None], on_round_phase_changed: Callable[[str], None] = None):
        self.on_match_start = on_match_start
        self.on_match_end = on_match_end
        self.on_real_match_end = on_real_match_end
        self.on_round_phase_changed = on_round_phase_changed
        self.is_in_match = False
        self.is_range = False
        self.phase_pattern_1 = re.compile(r"State:\s*\w+\s*->\s*(PreRound|InProgress|PostRound)")
        self.phase_pattern_2 = re.compile(r"Match State Changed from\s*\w+\s*to\s*(PreRound|InProgress|PostRound)")

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

                    # 試合開始の検知 (デスマッチ等もカバーするためトリガーを拡張)
                    is_start = (
                        "Broadcasting state changed to InGame" in line or
                        "Match State Changed from WaitingToStart to PreRound" in line or
                        "Match State Changed from WaitingToStart to InProgress" in line or
                        "State: WaitingToStart -> PreRound" in line or
                        "State: WaitingToStart -> InProgress" in line
                    )
                    
                    # 試合終了の検知
                    is_end = (
                        "Broadcasting state changed to TransitionToMainMenu" in line or
                        "Broadcasting state changed to PostGame" in line or
                        "Match State Changed from InProgress to WaitingPostMatch" in line or
                        "State: InProgress -> WaitingPostMatch" in line
                    )

                    if is_start and not self.is_in_match:
                        self.is_in_match = True
                        self.on_match_start(self.is_range)
                    elif is_end and self.is_in_match:
                        self.is_in_match = False
                        self.on_match_end(self.is_range)
                        
                    # ラウンドフェーズの検知
                    if self.is_in_match and self.on_round_phase_changed:
                        match = self.phase_pattern_1.search(line) or self.phase_pattern_2.search(line)
                        if match:
                            self.on_round_phase_changed(match.group(1))
            finally:
                f.close()
                            
        except PermissionError:
            raise PermissionError("Access denied. The log file might be locked by Vanguard.")