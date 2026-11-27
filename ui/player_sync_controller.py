"""Module: @role: 動画プレイヤー画面におけるタイムライン生成・手動同期調整・映像解析自動同期スレッドを制御するコントローラ"""
import json
import os
from typing import Callable, Optional
from PyQt6.QtCore import QObject, Qt, pyqtSlot
from PyQt6.QtWidgets import QPushButton
from core.config import Config
from core.i18n import Translations
from ui.clip_edit_panel import ClipEditPanel
from ui.in_app_notification import InAppNotification
from ui.player_navigation_controller import PlayerNavigationController
from ui.timeline_auto_sync import AutoSyncResult, TimelineAutoSyncThread
from ui.timeline_builder import build_timeline_data, extract_player_sync_events
from ui.timeline_overlay import TimelineOverlay
from ui.timeline_sync_panel import TimelineSyncPanel
from ui.video_player_core import VideoPlayerCore

class PlayerSyncController(QObject):
    def __init__(
        self,
        config: Config,
        t: Translations,
        player_core: VideoPlayerCore,
        timeline_overlay: TimelineOverlay,
        sync_panel: TimelineSyncPanel,
        sync_btn: QPushButton,
        edit_panel: ClipEditPanel,
        nav_controller: PlayerNavigationController,
        notification: InAppNotification,
        format_time_fn: Callable[[int], str],
        get_load_sequence_fn: Callable[[], int],
        on_close_edit_panel: Optional[Callable[[], None]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.t = t
        self.player_core = player_core
        self.timeline_overlay = timeline_overlay
        self.sync_panel = sync_panel
        self.sync_btn = sync_btn
        self.edit_panel = edit_panel
        self.nav_controller = nav_controller
        self.notification = notification
        self.format_time = format_time_fn
        self._get_load_sequence = get_load_sequence_fn
        self._on_close_edit_panel = on_close_edit_panel

        self.current_json_filename: Optional[str] = None
        self.current_match_data: Optional[dict] = None
        self._current_video_path: Optional[str] = None
        self.current_rounds: list[dict] = []

        self._auto_sync_thread: Optional[TimelineAutoSyncThread] = None
        self._active_sync_threads: set[TimelineAutoSyncThread] = set()
        self._thread_notify_flags: dict[TimelineAutoSyncThread, bool] = {}
        self._last_auto_sync_seq: Optional[int] = None

        self.sync_panel.offsetDeltaRequested.connect(self._on_sync_offset_delta)
        self.sync_panel.alignEventRequested.connect(self._on_sync_align_event)
        self.sync_panel.autoSyncRequested.connect(self._on_manual_auto_sync_requested)
        self.sync_panel.resetRequested.connect(self._on_sync_reset)
        self.sync_panel.closeRequested.connect(self.toggle_sync_panel)

    def _update_sync_btn_style(self, active: bool) -> None:
        bg = "#0078D4" if active else "#333333"
        hover = "#1084E0" if active else "#444444"
        self.sync_btn.setStyleSheet(
            f"QPushButton {{ border-radius: 20px; background-color: {bg}; }}"
            f"QPushButton:hover {{ background-color: {hover}; }}"
        )

    def reset_for_prepare(self) -> None:
        self.stop_auto_sync()
        self.current_json_filename = None
        self.current_match_data = None
        self._current_video_path = None
        self.current_rounds = []
        self.nav_controller.set_current_rounds([])
        self.sync_panel.setVisible(False)
        self._update_sync_btn_style(False)

    def set_current_recording(self, json_filename: Optional[str], match_data: Optional[dict]) -> None:
        self.current_json_filename = json_filename
        self.current_match_data = match_data

    def set_video_path(self, video_path: Optional[str]) -> None:
        self._current_video_path = video_path

    def stop_auto_sync(self) -> None:
        # UIスレッドをwait()でブロックせず、killとfinishedシグナル経由で安全に破棄する
        for thread in list(self._active_sync_threads):
            thread.cancel()
        self._auto_sync_thread = None
        self.sync_panel.set_auto_sync_busy(False)

    def update_timeline_data(self, duration_ms: int) -> None:
        if not self.current_match_data:
            return

        match_info = self.current_match_data.get("match_info", self.current_match_data)
        riot_id = getattr(self.config, "RIOT_ID", "")
        tag_line = getattr(self.config, "TAG_LINE", "")
        known_accounts = list(getattr(self.config, "KNOWN_ACCOUNTS", []) or [])

        rounds, events = build_timeline_data(
            match_info, duration_ms, riot_id, tag_line, known_accounts=known_accounts
        )

        self.current_rounds = rounds
        self.nav_controller.set_current_rounds(rounds)
        self.timeline_overlay.set_data(rounds, events)
        self._refresh_sync_panel(duration_ms)

    def toggle_sync_panel(self) -> None:
        show = not self.sync_panel.isVisible()
        if show and self.edit_panel.isVisible() and self._on_close_edit_panel is not None:
            self._on_close_edit_panel()
        self.sync_panel.setVisible(show)
        self._update_sync_btn_style(show)

    def close_sync_panel(self) -> None:
        if self.sync_panel.isVisible():
            self.toggle_sync_panel()

    def maybe_start_auto_sync(self, duration_ms: int) -> None:
        seq = self._get_load_sequence()
        if not self.current_match_data or not self._current_video_path or duration_ms <= 0:
            return
        if self._last_auto_sync_seq == seq:
            return
        match_info = self.current_match_data.get("match_info", self.current_match_data)
        if match_info.get("timeline_sync_source") in ("manual", "auto"):
            return
        if self._has_valid_local_round_events(match_info, duration_ms):
            return
        if not (match_info.get("kills") or []):
            return

        self._last_auto_sync_seq = seq
        self._start_auto_sync_thread(duration_ms, wide_search=False, notify_on_fail=False)

    def _refresh_sync_panel(self, duration_ms: int) -> None:
        if not self.current_match_data:
            return
        match_info = self.current_match_data.get("match_info", self.current_match_data)
        cur_offset = int(match_info.get("timeline_sync_offset_ms") or 0)
        cur_source = str(match_info.get("timeline_sync_source") or "")
        self.sync_panel.update_offset_display(cur_offset, cur_source)

        riot_id = getattr(self.config, "RIOT_ID", "")
        tag_line = getattr(self.config, "TAG_LINE", "")
        known_accounts = list(getattr(self.config, "KNOWN_ACCOUNTS", []) or [])
        base_events = extract_player_sync_events(
            match_info, duration_ms, riot_id, tag_line, known_accounts=known_accounts
        )

        combo_events = []
        type_icon = {"kill": "💥", "death": "💀", "assist": "🤝"}
        for idx, ev in enumerate(base_events, start=1):
            ev_type = ev.get("type", "")
            t_actual = int(ev.get("base_time_ms", 0)) + cur_offset
            t_disp = f"-{self.format_time(-t_actual)}" if t_actual < 0 else self.format_time(t_actual)
            combo_events.append({
                "base_time_ms": int(ev.get("base_time_ms", 0)),
                "label": f"#{idx} {t_disp} {type_icon.get(ev_type, '')} {ev_type.capitalize()}",
            })
        self.sync_panel.set_events(combo_events, self.format_time)

    def _persist_and_refresh_timeline(self) -> None:
        if not self.current_match_data or not self.current_json_filename:
            return
        json_path = os.path.join(self.config.SAVE_DIR, self.current_json_filename)
        tmp_path = f"{json_path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.current_match_data, f, ensure_ascii=False, indent=4)
            os.replace(tmp_path, json_path)
        except OSError:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        self.update_timeline_data(self.player_core.duration())

    def _save_timeline_sync(self, offset_ms: int, source: str) -> None:
        if not self.current_match_data or not self.current_json_filename:
            return
        match_info = self.current_match_data.get("match_info", self.current_match_data)
        match_info["timeline_sync_offset_ms"] = int(offset_ms)
        match_info["timeline_sync_source"] = source
        self._persist_and_refresh_timeline()

    def _on_sync_offset_delta(self, delta_ms: int) -> None:
        if not self.current_match_data:
            return
        match_info = self.current_match_data.get("match_info", self.current_match_data)
        cur = int(match_info.get("timeline_sync_offset_ms") or 0)
        self._save_timeline_sync(cur + delta_ms, "manual")

    def _on_sync_align_event(self, base_time_ms: int) -> None:
        if not self.current_match_data:
            return
        cur_pos_ms = int(self.player_core.position())
        new_offset_ms = cur_pos_ms - int(base_time_ms)
        self._save_timeline_sync(new_offset_ms, "manual")

    def _on_sync_reset(self) -> None:
        if not self.current_match_data or not self.current_json_filename:
            return
        match_info = self.current_match_data.get("match_info", self.current_match_data)
        match_info.pop("timeline_sync_offset_ms", None)
        match_info.pop("timeline_sync_source", None)
        self._persist_and_refresh_timeline()

    def _has_valid_local_round_events(self, match_info: dict, duration_ms: int) -> bool:
        raw_events = match_info.get("local_round_events") or []
        if not raw_events:
            return False
        rec_start = match_info.get("local_recording_start_time_ms")
        if not rec_start:
            rec_start = int(float(match_info.get("local_match_start_time") or 0) * 1000)
        max_span = max(duration_ms, 3600000)
        for ev in raw_events:
            if ev.get("phase") != "InProgress":
                continue
            t = ev.get("time_ms", 0)
            if t < 1000000000000:
                t += rec_start
            if (rec_start - 300000) <= t <= (rec_start + max_span + 300000):
                return True
        return False

    def _on_manual_auto_sync_requested(self) -> None:
        duration_ms = self.player_core.duration()
        if not self.current_match_data or not self._current_video_path or duration_ms <= 0:
            return
        self._start_auto_sync_thread(duration_ms, wide_search=True, notify_on_fail=True)

    def _start_auto_sync_thread(self, duration_ms: int, wide_search: bool, notify_on_fail: bool) -> None:
        self.stop_auto_sync()
        match_info = self.current_match_data.get("match_info", self.current_match_data)
        riot_id = getattr(self.config, "RIOT_ID", "")
        tag_line = getattr(self.config, "TAG_LINE", "")
        known_accounts = list(getattr(self.config, "KNOWN_ACCOUNTS", []) or [])

        self.sync_panel.set_auto_sync_busy(True)
        thread = TimelineAutoSyncThread(
            load_sequence=self._get_load_sequence(),
            video_path=self._current_video_path,
            match_info=dict(match_info),
            duration_ms=duration_ms,
            riot_id=riot_id,
            tag_line=tag_line,
            known_accounts=known_accounts,
            wide_search=wide_search,
            parent=None,
        )
        self._thread_notify_flags[thread] = notify_on_fail
        # lambda接続によるDirectConnection化を防ぎ、確実にメインスレッドへキュー実行する
        thread.syncCompleted.connect(self._on_auto_sync_completed, Qt.ConnectionType.QueuedConnection)
        thread.finished.connect(self._on_sync_thread_finished, Qt.ConnectionType.QueuedConnection)
        self._active_sync_threads.add(thread)
        self._auto_sync_thread = thread
        thread.start()

    @pyqtSlot()
    def _on_sync_thread_finished(self) -> None:
        sender_obj = self.sender()
        if not isinstance(sender_obj, TimelineAutoSyncThread):
            return
        self._active_sync_threads.discard(sender_obj)
        self._thread_notify_flags.pop(sender_obj, None)
        if self._auto_sync_thread is sender_obj:
            self._auto_sync_thread = None
        sender_obj.deleteLater()

    @pyqtSlot(int, object)
    def _on_auto_sync_completed(self, seq: int, result: AutoSyncResult) -> None:
        sender_obj = self.sender()
        thread = sender_obj if isinstance(sender_obj, TimelineAutoSyncThread) else None
        if thread is None or thread is not self._auto_sync_thread:
            return
        if seq != self._get_load_sequence():
            return
        notify_on_fail = self._thread_notify_flags.get(thread, False)
        self.sync_panel.set_auto_sync_busy(False)
        if result.applied:
            self._save_timeline_sync(result.sync_offset_ms, "auto")
            sec = result.sync_offset_ms / 1000.0
            sign = "+" if sec >= 0 else ""
            msg = getattr(self.t, "sync_auto_applied", "✅ 映像とログを自動同期しました ({offset})").format(
                offset=f"{sign}{sec:.1f}s"
            )
            self.notification.show_message(msg, duration=4000)
        elif notify_on_fail:
            msg = getattr(
                self.t,
                "sync_auto_not_confident",
                "⚠️ 自動同期の確信度が不足しています。手動ボタンまたはイベント基準合わせをご利用ください。",
            )
            self.notification.show_message(msg, duration=5000)
