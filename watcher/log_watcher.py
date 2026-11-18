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
    estimate_log_tz_offset,
    is_app_exit_line,
    is_menu_settled_line,
    is_menu_transition_line,
    is_pregame_transition_line,
    is_valorant_window_open,
    normalize_map_name,
    parse_log_timestamp,
    parse_map_update_from_line,
    parse_state_transition,
)

# メニュー遷移時にウィンドウクローズ(シャットダウン)でないことを確認する待機秒数
_MENU_END_VERIFY_DELAY_SETTLED_SEC = 1.5
_MENU_END_VERIFY_DELAY_TRANSITION_SEC = 3.5
# 再起動後にメインメニューへ到達してからInGame再接続が来ない場合に前試合終了とみなす秒数
_RECONNECT_FROM_MENU_TIMEOUT_SEC = 15.0
# クライアント終了後に再起動・再接続が行われないまま放置された場合の最大待機秒数
_CLIENT_RECONNECT_MAX_WAIT_SEC = 180.0
# メニュー遷移による終了発火直後に遅延LogExitが来た際、巻き戻しを許容する最大秒数
_CLIENT_EXIT_REVERT_WINDOW_SEC = 5.0


class LogWatcher:
    def __init__(
        self,
        on_match_start: Callable[[bool, float, bool], None],
        on_match_end: Callable[[bool], None],
        on_real_match_end: Callable[[], None],
        on_round_phase_changed: Optional[Callable[[str, float], None]] = None,
        on_ability_used: Optional[Callable[[float], None]] = None,
        on_performance_drop: Optional[Callable[[], None]] = None,
        on_client_closed: Optional[Callable[[], Optional[bool]]] = None,
        window_checker: Optional[Callable[[], Optional[bool]]] = None,
    ):
        self.on_match_start = on_match_start
        self.on_match_end = on_match_end
        self.on_real_match_end = on_real_match_end
        self.on_round_phase_changed = on_round_phase_changed
        self.on_ability_used = on_ability_used
        self.on_performance_drop = on_performance_drop
        self.on_client_closed = on_client_closed
        self._window_checker = window_checker or is_valorant_window_open
        self.is_in_match = False
        self.is_match_ending = False
        self.has_entered_in_progress = False
        self.is_range = False
        self.current_map_name = "Unknown"
        self.last_valid_map_name = "Unknown"
        self._stop_requested = False
        self._pending_match_start: Optional[Tuple[float, bool, float]] = None
        self._pending_match_end: Optional[Tuple[bool, float, bool]] = None
        self._is_client_closing = False
        self._awaiting_match_reconnect = False
        self._reconnect_menu_entered_at: Optional[float] = None
        self._client_closed_at: Optional[float] = None
        self._window_observed_in_match = False
        self._last_end_was_explicit = False
        self._match_end_triggered_at = 0.0
        self._pre_end_snapshot: Tuple[bool, str, bool, str] = (False, "Unknown", False, "Unknown")
        self._range_skip_notified = False
        self._tz_offset: Optional[float] = None

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
        self.last_valid_map_name = "Unknown"
        self.is_range = False
        self._pending_match_start = None
        self._pending_match_end = None
        self._range_skip_notified = False

    def _refresh_window_observation(self) -> Optional[bool]:
        try:
            state = self._window_checker()
        except Exception:
            state = None
        if state is True and self.is_in_match:
            self._window_observed_in_match = True
        return state

    def _is_window_closed_after_observed(self) -> bool:
        state = self._refresh_window_observation()
        return bool(self._window_observed_in_match and state is False)

    def _is_different_valid_map(self, map_a: str, map_b: str) -> bool:
        if not map_a or not map_b or map_a == "Unknown" or map_b == "Unknown":
            return False
        return normalize_map_name(map_a).lower() != normalize_map_name(map_b).lower()

    def _handle_client_exit(self) -> None:
        # ウィンドウを閉じた場合はマッチ終了扱いにせず、試合中状態を維持して録画を継続する
        self._is_client_closing = True
        self._pending_match_end = None
        self._reconnect_menu_entered_at = None
        self._window_observed_in_match = False
        if self.is_in_match:
            self._awaiting_match_reconnect = True
            if self._client_closed_at is None:
                self._client_closed_at = time.time()
            if self.on_client_closed:
                self.on_client_closed()
            return

        can_revert_recent_end = (
            self.is_match_ending
            and not self._last_end_was_explicit
            and (time.time() - self._match_end_triggered_at <= _CLIENT_EXIT_REVERT_WINDOW_SEC)
        )
        if can_revert_recent_end and self.on_client_closed and self.on_client_closed():
            prev_progress, prev_map, prev_range, prev_valid_map = self._pre_end_snapshot
            self.is_in_match = True
            self.is_match_ending = False
            self.has_entered_in_progress = prev_progress
            self.current_map_name = prev_map
            self.is_range = prev_range
            self.last_valid_map_name = prev_valid_map
            self._awaiting_match_reconnect = True
            if self._client_closed_at is None:
                self._client_closed_at = time.time()
        else:
            self.is_match_ending = False

    def _trigger_match_end(self, was_range: bool, is_explicit: bool) -> None:
        self._pre_end_snapshot = (
            self.has_entered_in_progress,
            self.current_map_name,
            self.is_range,
            self.last_valid_map_name,
        )
        self._match_end_triggered_at = time.time()
        self._pending_match_end = None
        self._pending_match_start = None
        self._range_skip_notified = False
        self._awaiting_match_reconnect = False
        self._reconnect_menu_entered_at = None
        self._client_closed_at = None
        self._window_observed_in_match = False
        self._last_end_was_explicit = is_explicit
        self.is_in_match = False
        self.is_match_ending = True
        self.has_entered_in_progress = False
        self.on_match_end(was_range)
        if not is_explicit:
            self.current_map_name = "Unknown"
            self.is_range = False

    def _flush_pending_match_end(self, force: bool = False) -> None:
        if self._pending_match_end is None:
            return
        if not self.is_in_match or self._is_client_closing or self._awaiting_match_reconnect:
            self._pending_match_end = None
            return
        if not force and self._is_window_closed_after_observed():
            logging.info("[LogWatcher] VALORANT window closed during menu transition. Keeping recording active.")
            self._handle_client_exit()
            return
        was_range, queued_at, is_settled = self._pending_match_end
        required_delay = (
            _MENU_END_VERIFY_DELAY_SETTLED_SEC
            if is_settled
            else _MENU_END_VERIFY_DELAY_TRANSITION_SEC
        )
        if force or (time.time() - queued_at >= required_delay):
            self._trigger_match_end(was_range, is_explicit=False)

    def _check_reconnect_timeout(self) -> None:
        if not self.is_in_match or not self._awaiting_match_reconnect:
            return
        now = time.time()
        # クライアントを閉じたまま再起動・再接続せず放置された場合の上限タイムアウト
        if (
            self._client_closed_at is not None
            and (now - self._client_closed_at >= _CLIENT_RECONNECT_MAX_WAIT_SEC)
        ):
            logging.info("[LogWatcher] Reconnect wait exceeded max timeout without match resume. Finalizing match.")
            self._is_client_closing = False
            self._trigger_match_end(was_range=False, is_explicit=False)
            return
        if self._is_client_closing or self._reconnect_menu_entered_at is None:
            return
        if self._is_window_closed_after_observed():
            self._handle_client_exit()
            return
        if now - self._reconnect_menu_entered_at >= _RECONNECT_FROM_MENU_TIMEOUT_SEC:
            logging.info("[LogWatcher] Stayed in MainMenu after restart without match reconnect. Finalizing previous match.")
            self._trigger_match_end(was_range=False, is_explicit=False)

    def _update_map_state_from_line(self, line: str, is_initial_scan: bool = False) -> bool:
        next_map, next_range, updated_real_map, reset_menu = parse_map_update_from_line(
            line, self.current_map_name, self.is_range
        )
        if reset_menu:
            if not self.is_in_match:
                self._reset_map_and_pending_state()
            elif (
                self._awaiting_match_reconnect
                and not self._is_client_closing
                and self._reconnect_menu_entered_at is None
            ):
                self._reconnect_menu_entered_at = time.time()
            return False

        # 通常試合中(再接続待機中を含む)に射撃場または別マップへ遷移した場合は先に前試合を終了確定させる
        if not is_initial_scan and self.is_in_match and not self.is_range:
            is_diff_map = not next_range and self._is_different_valid_map(next_map, self.last_valid_map_name)
            if next_range or is_diff_map:
                self._trigger_match_end(was_range=False, is_explicit=True)
                self.is_match_ending = False

        self.current_map_name = next_map
        self.is_range = next_range
        if (
            not is_initial_scan
            and (self.is_in_match or self._pending_match_start is not None)
            and next_map
            and next_map != "Unknown"
            and not next_range
        ):
            self.last_valid_map_name = next_map
        return updated_real_map

    def _scan_recent_log_state(self, f, log_path: Optional[str] = None) -> None:
        # 監視開始時に既存ログの末尾を走査し、既に射撃場にいる場合の誤録画を防ぐ
        try:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            read_size = min(size, 512 * 1024)
            f.seek(max(0, size - read_size), os.SEEK_SET)
            lines = f.read().splitlines()
            ref_epoch = time.time()
            if log_path and os.path.exists(log_path):
                try:
                    ref_epoch = os.path.getmtime(log_path)
                except OSError:
                    pass
            for rev_line in reversed(lines[-200:]):
                if self.time_pattern.match(rev_line):
                    raw_ts = parse_log_timestamp(rev_line)
                    estimated = estimate_log_tz_offset(raw_ts, ref_epoch)
                    if estimated is not None:
                        self._tz_offset = estimated
                    break
            for raw_line in lines:
                line = raw_line.strip()
                if not line:
                    continue
                self._update_map_state_from_line(line, is_initial_scan=True)
                if is_menu_transition_line(line) or is_app_exit_line(line):
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
            if self.current_map_name != "Unknown":
                self.last_valid_map_name = self.current_map_name
            self._pending_match_start = None
            self.on_match_start(False, log_time, is_recovery)

    def _parse_log_time(self, line: str) -> float:
        if self._tz_offset is None and self.time_pattern.match(line):
            raw_ts = parse_log_timestamp(line)
            estimated = estimate_log_tz_offset(raw_ts, time.time())
            if estimated is not None:
                self._tz_offset = estimated
        return parse_log_timestamp(line, tz_offset=self._tz_offset)

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
            self.last_valid_map_name = self.current_map_name
            self._pending_match_start = None
            self.on_match_start(False, log_time, is_recovery)

    def _process_log_line(self, line: str) -> None:
        if self._update_map_state_from_line(line):
            self._flush_pending_match_start()

        # 無関係なログ行での正規表現・時刻パース処理をスキップしてCPU負荷を削減
        if not any(k in line for k in RELEVANT_LOG_KEYWORDS):
            return

        # ボイスや通信・パーティー等別モジュールの状態遷移・切断ログによる誤検知を排除
        if self.ignored_module_pattern.search(line):
            return

        if is_app_exit_line(line):
            logging.info(f"[LogWatcher] Detected VALORANT window close / engine exit log: {line}")
            self._handle_client_exit()
            return

        if "Log file open" in line:
            self._is_client_closing = False
            self._reconnect_menu_entered_at = None
            self._window_observed_in_match = False
            if self.is_in_match:
                self._awaiting_match_reconnect = True
                self._pending_match_end = None
                if self._client_closed_at is None:
                    self._client_closed_at = time.time()
            return

        log_time = self._parse_log_time(line)
        from_state, to_state = parse_state_transition(line)
        is_menu = is_menu_transition_line(line, to_state)
        is_settled_menu = is_menu_settled_line(line, to_state)
        is_pregame = is_pregame_transition_line(line, to_state)
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

        if is_menu and not self.is_in_match:
            self.is_match_ending = False
            self._pending_match_start = None
            self._range_skip_notified = False
            self.current_map_name = "Unknown"
            self.is_range = False

        is_start = any(p in line for p in START_LOG_PATTERNS) or (
            to_state in ("PreRound", "InProgress") and from_state == "WaitingToStart"
        )
        is_end_log = any(p in line for p in END_LOG_PATTERNS) or (
            to_state in ("WaitingPostMatch", "PostGame")
        )

        # 新規試合のエージェント選択(Pregame)へ入った場合、保留中の終了や再接続待ちの前試合を即座に終了確定する
        if is_pregame:
            if self.is_in_match:
                if self._pending_match_end is not None:
                    self._flush_pending_match_end(force=True)
                elif self._awaiting_match_reconnect:
                    self._trigger_match_end(was_range=False, is_explicit=True)
            self.is_match_ending = False
            return

        if is_start or is_progress:
            # メニュー遷移の検証保留中に次の試合が開始・進行した場合は前試合の終了を先に確定させる
            if self._pending_match_end is not None:
                self._flush_pending_match_end(force=True)
                self.is_match_ending = False

            self._is_client_closing = False
            self._reconnect_menu_entered_at = None
            self._refresh_window_observation()

            if self._awaiting_match_reconnect and self.is_in_match:
                is_diff_map = self._is_different_valid_map(self.current_map_name, self.last_valid_map_name)
                if self.is_range_detected() or is_diff_map:
                    # 再起動後に元の試合ではなく射撃場や別マップへ入った場合は前試合を終了確定させ新試合処理へ進める
                    self._trigger_match_end(was_range=False, is_explicit=True)
                    self.is_match_ending = False
                else:
                    logging.info("[LogWatcher] Reconnected to ongoing VALORANT match. Continuing recording.")
                    self._awaiting_match_reconnect = False
                    self._client_closed_at = None
                    self.is_match_ending = False
                    if self.current_map_name == "Unknown" and self.last_valid_map_name != "Unknown":
                        self.current_map_name = self.last_valid_map_name
                    if round_phase and self.on_round_phase_changed:
                        self.on_round_phase_changed(round_phase, log_time)
                    if is_ability and self.on_ability_used:
                        self.on_ability_used(log_time)
                    return

            if not self.is_in_match and (
                to_state == "PreRound"
                or "Broadcasting state changed to InGame" in line
                or "Loopstate changed from PREGAME to INGAME" in line
            ):
                self.is_match_ending = False

        if is_start:
            self.is_match_ending = False
            if not self.is_in_match:
                self.is_in_match = True
                self.has_entered_in_progress = False
                self._refresh_window_observation()
                self._queue_or_trigger_match_start(log_time, is_recovery=False)
        elif is_progress and not self.is_in_match and not self.is_match_ending:
            self.is_in_match = True
            self.has_entered_in_progress = True
            self._refresh_window_observation()
            self._queue_or_trigger_match_start(log_time, is_recovery=True)
        elif self.is_in_match:
            if is_end_log and (self.has_entered_in_progress or self._awaiting_match_reconnect):
                self._is_client_closing = False
                self._trigger_match_end(self.is_range_detected(), is_explicit=True)
            elif self._awaiting_match_reconnect:
                if is_settled_menu and not self._is_client_closing and self._reconnect_menu_entered_at is None:
                    self._reconnect_menu_entered_at = time.time()
            elif not self._is_client_closing and is_menu:
                if self._is_window_closed_after_observed():
                    logging.info("[LogWatcher] Ignore menu transition because VALORANT window is closed.")
                    self._handle_client_exit()
                else:
                    was_range = self.is_range_detected()
                    # TransitionToMainMenu等のメニュー遷移はウィンドウ終了時にも出るためシャットダウン有無を検証してから確定する
                    if self._pending_match_end is None:
                        self._pending_match_end = (was_range, time.time(), is_settled_menu)
                    elif is_settled_menu and not self._pending_match_end[2]:
                        self._pending_match_end = (was_range, time.time(), True)

        if self._pending_match_start is not None:
            self._flush_pending_match_start()
        if self._pending_match_end is not None:
            self._flush_pending_match_end()
        if self._awaiting_match_reconnect:
            self._check_reconnect_timeout()

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
            time.sleep(0.2)

        logging.info(f"Started watching log file: {log_path}")

        try:
            f = open(log_path, "r", encoding="utf-8", errors="replace")
            self._scan_recent_log_state(f, log_path=log_path)

            try:
                while not self._stop_requested:
                    line = f.readline()
                    if not line:
                        self._flush_pending_match_start()
                        self._flush_pending_match_end()
                        self._check_reconnect_timeout()
                        if self.is_in_match:
                            if not self._window_observed_in_match:
                                self._refresh_window_observation()
                            elif (
                                not self._is_client_closing
                                and not self._awaiting_match_reconnect
                                and self._is_window_closed_after_observed()
                            ):
                                logging.info("[LogWatcher] VALORANT window closed without exit log. Keeping recording active.")
                                self._handle_client_exit()
                        # EOF到達時のみファイルサイズ縮小(Valorant再起動)をチェックしI/Oコールを最小化
                        try:
                            current_size = os.path.getsize(log_path)
                            if current_size < f.tell():
                                logging.info("Log file was cleared (Valorant restarted). Reopening...")
                                new_f = open(log_path, "r", encoding="utf-8", errors="replace")
                                try:
                                    f.close()
                                except OSError:
                                    pass
                                f = new_f
                                self._tz_offset = None
                                self._is_client_closing = False
                                self._pending_match_end = None
                                self._reconnect_menu_entered_at = None
                                self._window_observed_in_match = False
                                if self.is_in_match:
                                    # 試合中にVALORANTを再起動した場合は録画を止めず再接続待機に移行する
                                    self._awaiting_match_reconnect = True
                                    self._pending_match_start = None
                                    if self._client_closed_at is None:
                                        self._client_closed_at = time.time()
                                else:
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
