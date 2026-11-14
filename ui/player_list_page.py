"""Module: @role: 録画一覧画面の全体レイアウト、検索・フィルタ・ソート制御、戦績サマリー、および一括操作バーを統括するページ"""
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QLabel,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QByteArray, QTimer
from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer
from core.config import Config
from core.i18n import get_trans
from ui.record_data_loader import RecordDataLoader
from ui.in_app_notification import InAppNotification
from ui.player_list_header import PlayerListHeader
from ui.player_list_renderer import PlayerListRenderer
from ui.player_list_actions import (
    LogRefetchThread,
    delete_single_record_files,
    prompt_and_rename_record,
    reveal_record_in_explorer,
    set_record_fetching_state,
    show_record_filter_menu,
    show_record_sort_menu,
    toggle_favorite_status,
)

FILTER_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M10 18h4v-2h-4v2zM3 6v2h18V6H3zm3 7h12v-2H6v2z"/>
</svg>"""

SORT_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M3 18h6v-2H3v2zM3 6v2h18V6H3zm0 7h12v-2H3v2z"/>
</svg>"""


def _make_svg_icon(svg_bytes: bytes, size: int = 18) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class PlayerListPage(QWidget):
    settingsRequested = pyqtSignal()
    recordSelected = pyqtSignal(str)
    listRefreshed = pyqtSignal()

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.t = get_trans(getattr(self.config, "LANGUAGE", "en"))
        self._is_ja = getattr(self.t, "mode_all", "All") == "全て"

        self.delete_mode = False
        self.favorite_mode = False
        self.current_filter = None
        self.current_sort = "date_desc"
        self.current_mode = "All"
        self.search_query = ""
        self.available_agents = set()
        self.available_maps = set()
        self.all_loaded_records = []
        self._needs_refresh = False
        self._refresh_scheduled = False
        self._refetch_threads: dict[str, LogRefetchThread] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        # 1. 上段ナビゲーションヘッダー
        self.header = PlayerListHeader(self.t, self)
        self.header.tabChanged.connect(self._on_tab_clicked)
        self.header.deleteModeToggled.connect(self.toggle_delete_mode)
        self.header.favoriteModeToggled.connect(self.toggle_favorite_mode)
        self.header.settingsRequested.connect(self.settingsRequested)
        self.header.searchChanged.connect(self._on_search_changed)
        layout.addWidget(self.header)

        # 2. 中段サブツールバー (件数・勝率サマリー、アクティブフィルターチップ、フィルター・ソートボタン)
        self.toolbar_widget = QWidget()
        self.toolbar_widget.setStyleSheet("background: transparent;")
        toolbar_layout = QHBoxLayout(self.toolbar_widget)
        toolbar_layout.setContentsMargins(2, 0, 2, 4)
        toolbar_layout.setSpacing(8)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet(
            "color: #9FB1C4; font-size: 12px; font-weight: 600; background: transparent;"
        )
        toolbar_layout.addWidget(self.summary_label)

        # アクティブフィルター解除チップ
        self.active_filter_chip = QPushButton("")
        self.active_filter_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.active_filter_chip.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 70, 85, 0.2);
                color: #FF8A93;
                border: 1px solid #FF4655;
                border-radius: 11px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #FF4655;
                color: #FFFFFF;
            }
        """)
        self.active_filter_chip.clicked.connect(lambda: self.apply_filter(None))
        self.active_filter_chip.hide()
        toolbar_layout.addWidget(self.active_filter_chip)

        toolbar_layout.addStretch()

        # フィルターボタン
        self.filter_btn = QPushButton(" フィルター" if self._is_ja else " Filter")
        self.filter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.filter_btn.setFixedHeight(30)
        self.filter_btn.setIcon(_make_svg_icon(FILTER_SVG, 16))
        self.filter_btn.setIconSize(QSize(16, 16))
        self.filter_btn.clicked.connect(self.show_filter_menu)
        toolbar_layout.addWidget(self.filter_btn)

        # ソートボタン
        self.sort_btn = QPushButton()
        self.sort_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sort_btn.setFixedHeight(30)
        self.sort_btn.setIcon(_make_svg_icon(SORT_SVG, 16))
        self.sort_btn.setIconSize(QSize(16, 16))
        self.sort_btn.clicked.connect(self.show_sort_menu)
        toolbar_layout.addWidget(self.sort_btn)

        self._update_toolbar_button_styles()
        layout.addWidget(self.toolbar_widget)

        # 3. 一括操作アクションバー (削除モード / お気に入りモード時に出現)
        self.batch_bar = QWidget()
        self.batch_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.batch_bar.setStyleSheet("""
            QWidget {
                background-color: #1A2634;
                border: 1px solid #34495E;
                border-radius: 8px;
            }
        """)
        batch_layout = QHBoxLayout(self.batch_bar)
        batch_layout.setContentsMargins(14, 8, 14, 8)
        batch_layout.setSpacing(10)

        self.batch_status_label = QLabel("")
        self.batch_status_label.setStyleSheet(
            "color: #F0F4F8; font-size: 13px; font-weight: bold; border: none; background: transparent;"
        )
        batch_layout.addWidget(self.batch_status_label)

        batch_layout.addStretch()

        sub_btn_style = """
            QPushButton {
                background-color: #243447;
                color: #D0D8E0;
                border: 1px solid #3A4B5C;
                border-radius: 5px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2F4258;
                color: #FFFFFF;
            }
        """
        self.select_all_btn = QPushButton("すべて選択" if self._is_ja else "Select All")
        self.select_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_all_btn.setStyleSheet(sub_btn_style)
        self.select_all_btn.clicked.connect(lambda: self.renderer.set_all_checked(True))
        batch_layout.addWidget(self.select_all_btn)

        self.deselect_all_btn = QPushButton("選択解除" if self._is_ja else "Clear Selection")
        self.deselect_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.deselect_all_btn.setStyleSheet(sub_btn_style)
        self.deselect_all_btn.clicked.connect(lambda: self.renderer.set_all_checked(False))
        batch_layout.addWidget(self.deselect_all_btn)

        self.batch_apply_btn = QPushButton()
        self.batch_apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.batch_apply_btn.clicked.connect(self._execute_batch_action)
        batch_layout.addWidget(self.batch_apply_btn)

        self.batch_cancel_btn = QPushButton(getattr(self.t, "cancel", "キャンセル"))
        self.batch_cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.batch_cancel_btn.setStyleSheet(sub_btn_style)
        self.batch_cancel_btn.clicked.connect(self._cancel_selection_mode)
        batch_layout.addWidget(self.batch_cancel_btn)

        self.batch_bar.hide()
        layout.addWidget(self.batch_bar)

        # 4. スクロールエリア
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("""
            QScrollArea { 
                border: none; 
                background-color: transparent; 
            }
            QScrollBar:vertical {
                background: #121A24;
                width: 10px;
                margin: 0px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #3A4B5C;
                min-height: 24px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover {
                background: #5A7088;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)

        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background-color: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 4, 12)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)

        self.renderer = PlayerListRenderer(
            config=self.config,
            scroll_area=self.scroll_area,
            scroll_layout=self.scroll_layout,
            t=self.t,
            on_record_selected=self.recordSelected.emit,
            on_rename_requested=self.rename_record,
            on_delete_requested=self.delete_record,
            on_favorite_toggled=self._on_single_favorite_toggled,
            on_reveal_requested=self._on_reveal_requested,
            on_refetch_log_requested=self.refetch_record_log,
            on_selection_changed=self._update_batch_bar_state,
            on_reset_filters=self._reset_all_filters,
        )

        self.notification = InAppNotification(self)
        self.refresh_list()

    def _get_sort_display_text(self) -> str:
        mapping = {
            "date_desc": getattr(self.t, "sort_date_desc", "日付 (新しい順)"),
            "date_asc": getattr(self.t, "sort_date_asc", "日付 (古い順)"),
            "mmr_desc": getattr(self.t, "sort_mmr_desc", "MMR変動 (高い順)"),
            "mmr_asc": getattr(self.t, "sort_mmr_asc", "MMR変動 (低い順)"),
        }
        return mapping.get(self.current_sort, mapping["date_desc"])

    def _update_toolbar_button_styles(self) -> None:
        base_style = """
            QPushButton {
                background-color: #15202B;
                color: #D0D8E0;
                border: 1px solid #2A3A4B;
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #203040;
                color: #FFFFFF;
                border: 1px solid #5A7088;
            }
        """
        active_style = """
            QPushButton {
                background-color: rgba(255, 70, 85, 0.22);
                color: #FFFFFF;
                border: 1px solid #FF4655;
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(255, 70, 85, 0.35);
            }
        """
        self.filter_btn.setStyleSheet(active_style if self.current_filter else base_style)
        self.sort_btn.setStyleSheet(base_style)
        self.sort_btn.setText(f" {self._get_sort_display_text()} ▾")

        if self.current_filter:
            f_type, f_val = self.current_filter
            if f_type == "favorite":
                label = f"★ {self.t.filter_favorite}  ✕"
            elif f_type == "agent":
                label = f"{self.t.filter_agent}: {f_val}  ✕"
            elif f_type == "result":
                res_map = {"win": self.t.result_win, "loss": self.t.result_loss, "draw": self.t.result_draw}
                label = f"{self.t.filter_result}: {res_map.get(str(f_val), f_val)}  ✕"
            elif f_type == "map":
                label = f"{self.t.filter_map}: {f_val}  ✕"
            else:
                label = f"{f_type}: {f_val}  ✕"
            self.active_filter_chip.setText(label)
            self.active_filter_chip.show()
        else:
            self.active_filter_chip.hide()

    def _on_tab_clicked(self, mode: str) -> None:
        self.current_mode = mode
        self._apply_filters_and_render()
        self.listRefreshed.emit()

    def _on_search_changed(self, text: str) -> None:
        self.search_query = (text or "").strip().lower()
        self._apply_filters_and_render()

    def _reset_all_filters(self) -> None:
        self.current_filter = None
        self.search_query = ""
        self.current_mode = "All"
        self.header.search_input.blockSignals(True)
        self.header.search_input.clear()
        self.header.search_input.blockSignals(False)
        self.header._on_tab_clicked("All")
        self.refresh_list()

    def _apply_filters_and_render(self) -> None:
        self._update_toolbar_button_styles()
        filtered_records = []
        if hasattr(self, "all_loaded_records"):
            for rec in self.all_loaded_records:
                mode = rec.get("mode", "Unknown")
                if self.current_mode != "All" and mode.lower() != self.current_mode.lower():
                    continue
                if self.search_query:
                    searchable = " ".join([
                        str(rec.get("display_name", "")),
                        str(rec.get("custom_name", "")),
                        str(rec.get("map_name", "")),
                        str(rec.get("agent_name", "")),
                        str(rec.get("mode", "")),
                        " ".join(rec.get("party_members") or []),
                    ]).lower()
                    if self.search_query not in searchable:
                        continue
                filtered_records.append(rec)

        if self.current_sort == "date_desc":
            filtered_records.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        elif self.current_sort == "date_asc":
            filtered_records.sort(key=lambda x: x.get("timestamp", 0), reverse=False)
        elif self.current_sort == "mmr_desc":
            filtered_records.sort(key=lambda x: x.get("mmr_change", 0), reverse=True)
        elif self.current_sort == "mmr_asc":
            filtered_records.sort(key=lambda x: x.get("mmr_change", 0), reverse=False)

        self._update_summary_label(filtered_records)
        has_active_filter = bool(
            self.current_filter or self.search_query or self.current_mode != "All"
        )
        self.renderer.set_records(
            filtered_records,
            self.current_sort,
            self.delete_mode,
            self.favorite_mode,
            has_active_filter=has_active_filter,
        )
        self._update_batch_bar_state()

    def _update_summary_label(self, records: list[dict]) -> None:
        total = len(records)
        wins = sum(1 for r in records if r.get("result") == "win")
        losses = sum(1 for r in records if r.get("result") == "loss")
        decided = wins + losses
        if self._is_ja:
            text = f"全 {total} 件"
            if decided > 0:
                wr = int(round((wins / decided) * 100))
                text += f"   |   {wins}勝 {losses}敗  (勝率 {wr}%)"
        else:
            text = f"{total} Record(s)"
            if decided > 0:
                wr = int(round((wins / decided) * 100))
                text += f"   |   {wins}W {losses}L  ({wr}% WR)"
        self.summary_label.setText(text)

    def _update_batch_bar_state(self) -> None:
        active = self.delete_mode or self.favorite_mode
        self.batch_bar.setVisible(active)
        if not active:
            return

        selected_count = len(self.renderer.get_selected_files())
        if self.delete_mode:
            msg = (
                f"🗑  削除する録画を選択してください （選択中: {selected_count} 件）"
                if self._is_ja
                else f"🗑  Select recordings to delete ({selected_count} selected)"
            )
            btn_text = (
                f"選択した {selected_count} 件を削除" if self._is_ja else f"Delete ({selected_count})"
            )
            btn_color = "#FF4655"
            text_color = "#FFFFFF"
        else:
            msg = (
                f"★  お気に入りを切り替える録画を選択してください （選択中: {selected_count} 件）"
                if self._is_ja
                else f"★  Select recordings to toggle favorite ({selected_count} selected)"
            )
            btn_text = (
                f"選択した {selected_count} 件を切替"
                if self._is_ja
                else f"Toggle Favorite ({selected_count})"
            )
            btn_color = "#FFD700"
            text_color = "#111111"

        self.batch_status_label.setText(msg)
        self.batch_apply_btn.setText(btn_text)
        self.batch_apply_btn.setEnabled(selected_count > 0)
        self.batch_apply_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_color};
                color: {text_color};
                border: none;
                border-radius: 5px;
                padding: 5px 14px;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton:disabled {{
                background-color: #2B3948;
                color: #6C7D8E;
            }}
        """)

    def _execute_batch_action(self) -> None:
        if self.delete_mode:
            self.toggle_delete_mode(False)
        elif self.favorite_mode:
            self.toggle_favorite_mode(False)

    def _cancel_selection_mode(self) -> None:
        self.delete_mode = False
        self.favorite_mode = False
        self.header.set_delete_mode(False)
        self.header.set_favorite_mode(False)
        self.renderer.update_selection_mode(False, False)
        self._update_batch_bar_state()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "notification") and self.notification.isVisible():
            self.notification.move((self.width() - self.notification.width()) // 2, 20)
        if hasattr(self, "renderer"):
            QTimer.singleShot(0, self.renderer.check_need_more_items)

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_needs_refresh", False) or getattr(self.renderer, "displayed_count", 0) == 0:
            if not self._refresh_scheduled:
                self._refresh_scheduled = True
                QTimer.singleShot(0, self._run_scheduled_refresh)

    def _run_scheduled_refresh(self):
        self._refresh_scheduled = False
        if self.isVisible() and (
            getattr(self, "_needs_refresh", False) or getattr(self.renderer, "displayed_count", 0) == 0
        ):
            self.refresh_list()

    def toggle_delete_mode(self, checked):
        if checked:
            self.delete_mode = True
            self.favorite_mode = False
            self.header.set_delete_mode(True)
            self.header.set_favorite_mode(False)
            self.renderer.update_selection_mode(self.delete_mode, self.favorite_mode)
        else:
            selected_files = self.renderer.get_selected_files()
            if selected_files:
                title = "録画の削除" if self._is_ja else "Delete Records"
                msg = (
                    f"選択した {len(selected_files)} 件の録画データを削除してもよろしいですか？\nこの操作は取り消せません。"
                    if self._is_ja
                    else f"Are you sure you want to delete {len(selected_files)} selected record(s)?"
                )
                reply = QMessageBox.question(
                    self,
                    title,
                    msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    for json_filename in selected_files:
                        self._delete_single_record_files(json_filename)
                    self.delete_mode = False
                    self.header.set_delete_mode(False)
                    self.renderer.update_selection_mode(self.delete_mode, self.favorite_mode)
                    self.refresh_list()
                else:
                    self.header.set_delete_mode(True)
            else:
                self.delete_mode = False
                self.header.set_delete_mode(False)
                self.renderer.update_selection_mode(self.delete_mode, self.favorite_mode)
        self._update_batch_bar_state()

    def toggle_favorite_mode(self, checked):
        if checked:
            self.favorite_mode = True
            self.delete_mode = False
            self.header.set_favorite_mode(True)
            self.header.set_delete_mode(False)
            self.renderer.update_selection_mode(self.delete_mode, self.favorite_mode)
        else:
            selected_files = self.renderer.get_selected_files()
            if selected_files:
                for json_filename in selected_files:
                    self._toggle_favorite_status(json_filename)
            self.favorite_mode = False
            self.header.set_favorite_mode(False)
            self.renderer.update_selection_mode(self.delete_mode, self.favorite_mode)
            if selected_files:
                self.refresh_list()
        self._update_batch_bar_state()

    def _toggle_favorite_status(self, json_filename):
        return toggle_favorite_status(self.config.SAVE_DIR, json_filename)

    def _on_single_favorite_toggled(self, json_filename: str) -> None:
        new_state = self._toggle_favorite_status(json_filename)
        for rec in self.all_loaded_records:
            if rec.get("filename") == json_filename:
                rec["is_favorite"] = new_state
                break
        if self.current_filter and self.current_filter[0] == "favorite":
            self.refresh_list()

    def _on_reveal_requested(self, json_filename: str) -> None:
        reveal_record_in_explorer(self.config.SAVE_DIR, json_filename)

    def refetch_record_log(self, json_filename: str) -> None:
        if json_filename in self._refetch_threads:
            return
        set_record_fetching_state(self.config.SAVE_DIR, json_filename, True)
        self.refresh_list()
        start_msg = getattr(
            self.t,
            "refetching_log",
            "🔄 ログを再取得しています..." if self._is_ja else "🔄 Refetching match log...",
        )
        self.notification.show_message(start_msg, duration=3000)

        thread = LogRefetchThread(self.config, json_filename, is_ja=self._is_ja, parent=self)
        self._refetch_threads[json_filename] = thread
        thread.finishedRefetch.connect(self._on_refetch_log_finished)
        thread.start()

    def _on_refetch_log_finished(
        self, orig_filename: str, success: bool, _new_filename: str, message: str
    ) -> None:
        thread = self._refetch_threads.pop(orig_filename, None)
        if thread is not None:
            thread.deleteLater()
        self.refresh_list()
        self.listRefreshed.emit()
        self.notification.show_message(message, duration=4000 if success else 5000)

    def show_filter_menu(self):
        show_record_filter_menu(
            self, self.filter_btn, self.t, self.available_agents, self.available_maps, self.apply_filter
        )

    def apply_filter(self, filter_tuple):
        self.current_filter = filter_tuple
        self.refresh_list()

    def show_sort_menu(self):
        show_record_sort_menu(self, self.sort_btn, self.t, self.current_sort, self.apply_sort)

    def apply_sort(self, sort_key):
        self.current_sort = sort_key
        self._apply_filters_and_render()

    def _delete_single_record_files(self, json_filename):
        delete_single_record_files(self.config.SAVE_DIR, json_filename)

    def delete_record(self, json_filename):
        title = "録画の削除" if self._is_ja else "Delete Record"
        msg = (
            "この録画データを削除してもよろしいですか？\nこの操作は取り消せません。"
            if self._is_ja
            else "Are you sure you want to delete this record?"
        )
        reply = QMessageBox.question(
            self,
            title,
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._delete_single_record_files(json_filename)
            self.refresh_list()

    def rename_record(self, json_filename, current_name):
        if prompt_and_rename_record(self, self.config.SAVE_DIR, json_filename, current_name):
            self.refresh_list()

    def clear_list(self):
        self._needs_refresh = True
        self.renderer.clear()

    def refresh_list(self):
        # ウィンドウが非表示(ゲーム中など)の時にUIを再構築すると、
        # OSがフォーカス要求と誤認してゲームが裏画面に行くため、更新を保留する
        window = self.window()
        is_window_visible = window and not window.isHidden() and not window.isMinimized()
        is_current_widget = getattr(self.parent(), "currentWidget", lambda: self)() == self
        if not (self.isVisible() or (is_window_visible and is_current_widget)):
            self._needs_refresh = True
            return

        self._needs_refresh = False

        loader = RecordDataLoader(self.config)
        records_by_date, agents, maps = loader.load_records(self.current_filter)

        self.available_agents = agents
        self.available_maps = maps

        self.all_loaded_records = []
        for date_key in sorted(records_by_date.keys(), reverse=True):
            for rec in records_by_date[date_key]:
                rec["date_key"] = date_key
                self.all_loaded_records.append(rec)

        self._apply_filters_and_render()
