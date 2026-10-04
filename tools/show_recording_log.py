"""Module: @role: 録画エラーの最新診断ログを収集・表示するツール"""

import os
import sys

def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    diagnostic_path = os.path.join(project_root, "recording_diagnostic.log")
    
    if os.path.exists(diagnostic_path):
        print(f"=== [Recording Diagnostic Log] ({diagnostic_path}) ===")
        with open(diagnostic_path, "r", encoding="utf-8", errors="replace") as f:
            print(f.read())
        return

    app_log = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValorantRecorder', 'app.log')
    if os.path.exists(app_log):
        print(f"=== [App Log Tail] ({app_log}) ===")
        with open(app_log, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            print("".join(lines[-50:]))
    else:
        print("No log files found.")

if __name__ == "__main__":
    main()
