import os
import time
import subprocess
from typing import Callable

class LogWatcher:
    def __init__(self, on_match_start: Callable[[bool], None], on_match_end: Callable[[bool], None]):
        self.on_match_start = on_match_start
        self.on_match_end = on_match_end
        self.is_in_match = False
        self.is_range = False

    def start_watching(self):
        local_app_data = os.environ.get('LOCALAPPDATA')
        if not local_app_data:
            raise EnvironmentError("LOCALAPPDATA environment variable not found.")

        log_path = os.path.join(local_app_data, 'VALORANT', 'Saved', 'Logs', 'ShooterGame.log')
        
        if not os.path.exists(log_path):
            raise FileNotFoundError(f"Log file not found: {log_path}")

        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                f.seek(0, os.SEEK_END)
                
                while True:
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

                    # 試合開始と終了の検知（射撃訓練場フラグを渡す）
                    if "Broadcasting state changed to InGame" in line and not self.is_in_match:
                        self.is_in_match = True
                        self.on_match_start(self.is_range)
                    elif "Broadcasting state changed to TransitionToMainMenu" in line and self.is_in_match:
                        self.is_in_match = False
                        self.on_match_end(self.is_range)
                        
        except PermissionError:
            raise PermissionError("Access denied. The log file might be locked by Vanguard.")