"""Module: @role: ShooterGame.logを重要度別に分類し、貼り付け可能なサイズに分割出力するツール"""
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.log_split_utils import (
    EXCLUDE_LOG_PATTERNS,
    KNOWN_LOG_PATTERNS,
    POTENTIAL_LOG_KEYWORDS,
    get_valorant_log_path,
    save_split_log_files,
)


def main() -> None:
    log_path = get_valorant_log_path()
    if not log_path:
        print("LOCALAPPDATA environment variable not found.")
        return

    if not os.path.exists(log_path):
        print(f"Log file not found: {log_path}")
        return

    output_path = "filtered_log.txt"
    potential_path = "potential_logs.txt"
    useless_path = "useless_logs.txt"

    compiled_exclude = [re.compile(pattern) for pattern in EXCLUDE_LOG_PATTERNS]
    compiled_known = [re.compile(pattern) for pattern in KNOWN_LOG_PATTERNS]
    compiled_potential = [re.compile(pattern, re.IGNORECASE) for pattern in POTENTIAL_LOG_KEYWORDS]

    print(f"Reading log from: {log_path}")
    print("Filtering and splitting into pasteable files...")

    known_lines: list[str] = []
    potential_lines: list[str] = []
    useless_lines: list[str] = []
    total_lines_count = 0

    with open(log_path, "r", encoding="utf-8", errors="replace") as infile:
        for line in infile:
            total_lines_count += 1
            if any(pattern.search(line) for pattern in compiled_known):
                known_lines.append(line)
                continue
            if any(pattern.search(line) for pattern in compiled_exclude):
                continue
            if any(pattern.search(line) for pattern in compiled_potential):
                potential_lines.append(line)
            else:
                useless_lines.append(line)

    known_parts = save_split_log_files(output_path, known_lines)
    potential_parts = save_split_log_files(potential_path, potential_lines)
    useless_parts = save_split_log_files(useless_path, useless_lines)

    print("Done! Logs have been categorized and split into pasteable parts.")
    print(f"Total lines: {total_lines_count}")
    print(f" -> Known lines ({len(known_lines)}): {output_path} ({len(known_parts)} parts)")
    for p in known_parts:
        print(f"    * {p}")
    print(f" -> Potential lines ({len(potential_lines)}): {potential_path} ({len(potential_parts)} parts)")
    for p in potential_parts:
        print(f"    * {p}")
    print(f" -> Useless lines ({len(useless_lines)}): {useless_path} ({len(useless_parts)} parts)")


if __name__ == "__main__":
    main()
