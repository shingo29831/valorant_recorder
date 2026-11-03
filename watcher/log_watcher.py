"""Module: @role: ShooterGame.logのファイル監視ループとイベントコールバック発火を担うモジュール"""
import logging
import os
import time
from typing import Callable, Optional, Tuple
from watcher.log_parser import (
    END_LOG_PATTERNS,
    IGNORED_MODULE_PATTERN,
    MAP_NAME_PATTERN,
    PERF_WARNING_PATTERNS,
    RANGE_KEYWORD_PATTERN,
    RELEVANT_LOG_KEYWORDS,
    START_LOG_PATTERNS,
    STATE_TRANSITION_PATTERN,
    TIME_PATTERN,
    VALID_GAME_STATES,
    WORLD_MAP_PATTERN,
    is_menu_transition_line,
    parse_log_timestamp,
    parse_map_update_from_line,
    parse_state_transition,
)


class LogWatcher:
    def __init__(
        self,
        on_match_start: Callable[[bool, float, bool], None],
        on_match_end: Callable[[bool], None],
        on_real_match_end: Callable[[], None],
        on_round_phase_changed: Optional[Callable[[str, float], None]] = None,
        on_ability_used: Optional[Callable[[float], None]] = None,
        on_performance_drop: Optional[Callable[[], None]] = None,
    ):
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
        self._pending_match_start: Optional[Tuple[float, bool, float]] = None
        self._range_skip_notified = False

        self.map_name_pattern = MAP_NAME_PATTERN
        self.world_map_pattern = WORLD_MAP_PATTERN
        self.range_keyword_pattern = RANGE_KEYWORD_PATTERN
        self.state_transition_pattern = STATE_TRANSITION_PATTERN
        self.ignored_module_pattern = IGNORED_MODULE_PATTERN
        self.valid_game_states = VALID_GAME_STATES
        self.time_pattern = TIME_PATTERN

    def stop(self) -> None:
        self._stop_requested = True

    def is_range_detected(self) -> bool:
        return bool(self.is_range or self.range_keyword_pattern.search(self.current_map_name or ""))

    def _reset_map_and_pending_state(self) -> None:
        self.current_map_name = "Unknown"
        self.is_range = False
        self._pending_match_start = None
        self._range_skip_notified = False

    def _update_map_state_from_line(self, line: str) -> bool:
        next_map, next_range, updated_real_map, reset_menu = parse_map_update_from_line(
            line, self.current_map_name, self.is_range
        )
        if reset_menu:
            self._reset_map_and_pending_state()
            return False
        self.current_map_name = next_map
        self.is_range = next_range
        return updated_real_map

    def _scan_recent_log_state(self, f) -> None:
        # 監視開始時に既存ログの末尾を走査し、既に射撃場にいる場合の誤録画を防ぐ
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
                if is_menu_transition_line(line):
                    self._reset_map_and_pending_state()
        except OSError as e:
            logging.debug(f"Failed to scan recent log state: {e}")
        finally:
            try:
                f.seek(0, os.SEEK_END)
            except OSError:
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
        return parse_log_timestamp(line)

    def _queue_or_trigger_match_start(self, log_time: float, is_recovery: bool) -> None:
        if self.is_range_detected():
            self._pending_match_start = None
            if not self._range_skip_notified:
                self._range_skip_notified = True
                self.on_match_start(True, log_time, is_recovery)
        elif self.current_map_name == "Unknown":
            # マップ名ログが直後の行に出力されるケースに備えて開始判定を保留
            self._pending_match_start = (log_time, is_recovery, time.time())
        else:
            self._pending_match_start = None
            self.on_match_start(False, log_time, is_recovery)

    def _process_log_line(self, line: str) -> None:
        if self._update_map_state_from_line(line):
            self._flush_pending_match_start()

        # 無関係なログ行での正規表現・時刻パース処理をスキップしてCPU負荷を削減
        if not any(k in line for k in RELEVANT_LOG_KEYWORDS):
            return

        # ボイスや通信・パーティー等別モジュールの状態遷移ログによる誤検知を排除
        if self.ignored_module_pattern.search(line):
            return

        log_time = self._parse_log_time(line)
        from_state, to_state = parse_state_transition(line)
        is_menu = is_menu_transition_line(line, to_state)
        is_ability = "LogAbilitySystem:" in line and "Ability activated" in line

        is_progress = False
        round_phase = None

        if to_state in ("PreRound", "InProgress", "PostRound"):
            is_progress = True
            round_phase = to_state
            if to_state in ("InProgress", "PostRound"):
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
            if not self.is_in_match:
                self.current_map_name = "Unknown"
                self.is_range = False

        is_start = any(p in line for p in START_LOG_PATTERNS) or (
            to_state in ("PreRound", "InProgress") and from_state == "WaitingToStart"
        )
        is_end_log = any(p in line for p in END_LOG_PATTERNS) or (
            to_state in ("WaitingPostMatch", "PostGame")
        )

        is_end = bool(
            self.is_in_match and ((is_end_log and self.has_entered_in_progress) or is_menu)
        )

        if is_start:
            self.is_match_ending = False
            if not self.is_in_match:
                self.is_in_match = True
                self.has_entered_in_progress = False
                self._queue_or_trigger_match_start(log_time, is_recovery=False)
        elif is_progress and not self.is_in_match and not self.is_match_ending:
            self.is_in_match = True
            self.has_entered_in_progress = True
            self._queue_or_trigger_match_start(log_time, is_recovery=True)
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

        if self.is_in_match and round_phase and self.on_round_phase_changed:
            self.on_round_phase_changed(round_phase, log_time)

        if self.is_in_match and self.on_ability_used and is_ability:
            self.on_ability_used(log_time)

        if self.is_in_match and self.on_performance_drop:
            if any(w in line for w in PERF_WARNING_PATTERNS):
                self.on_performance_drop()

    def start_watching(self) -> None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            user_profile = os.environ.get("USERPROFILE")
            if user_profile:
                local_app_data = os.path.join(user_profile, "AppData", "Local")
            else:
                logging.error("LOCALAPPDATA and USERPROFILE environment variables not found.")
                raise EnvironmentError("LOCALAPPDATA environment variable not found.")

        log_path = os.path.join(local_app_data, "VALORANT", "Saved", "Logs", "ShooterGame.log")
        self._stop_requested = False

        # Valorant起動前にアプリが起動された場合もエラー終了せず生成を待機する
        while not os.path.exists(log_path):
            if self._stop_requested:
                return
            time.sleep(2.0)

        logging.info(f"Started watching log file: {log_path}")

        try:
            f = open(log_path, "r", encoding="utf-8", errors="replace")
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
                                f = open(log_path, "r", encoding="utf-8", errors="replace")
                                f.seek(0, os.SEEK_END)
                                self.is_in_match = False
                                self.has_entered_in_progress = False
                                self._reset_map_and_pending_state()
                                continue
                        except OSError:
                            pass
                        time.sleep(0.15)
                        continue

                    stripped = line.strip()
                    if stripped:
                        self._process_log_line(stripped)
            finally:
                f.close()

        except PermissionError as e:
            logging.error(f"PermissionError: Access denied to log file. {e}")
            raise PermissionError("Access denied. The log file might be locked by Vanguard.")
        except Exception as e:
            logging.error(f"Unexpected error in log watcher: {e}")
            raise
