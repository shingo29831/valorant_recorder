"""Module: @role: 録画一覧の日付グループ化描画、オンデマンド分割読み込み、Empty State表示、および選択状態管理を担うレンダラー"""
from typing import Callable, Optional
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from core.config import Config
from ui.flow_layout import FlowLayout
from ui.record_item_widget import RecordItemWidget


class PlayerListRenderer:
    def __init__(
        self,
        config: Config,
        scroll_area: QScrollArea,
        scroll_layout: QVBoxLayout,
        t: object,
        on_record_selected: Callable[[str], None],
        on_rename_requested: Callable[[str, str], None],
        on_delete_requested: Callable[[str], None],
        on_favorite_toggled: Optional[Callable[[str], None]] = None,
        on_reveal_requested: Optional[Callable[[str], None]] = None,
        on_selection_changed: Optional[Callable[[], None]] = None,
        on_reset_filters: Optional[Callable[[], None]] = None,
    ) -> None:
        self.config = config
        self.scroll_area = scroll_area
        self.scroll_layout = scroll_layout
        self.t = t
        self.on_record_selected = on_record_selected
        self.on_rename_requested = on_rename_requested
        self.on_delete_requested = on_delete_requested
        self.on_favorite_toggled = on_favorite_toggled
        self.on_reveal_requested = on_reveal_requested
        self.on_selection_changed = on_selection_changed
        self.on_reset_filters = on_reset_filters

        self.filtered_records: list[dict] = []
        self._date_group_stats: dict[str, dict] = {}
        self.displayed_count = 0
        self.current_group_key: Optional[str] = None
        self.current_flow_layout: Optional[FlowLayout] = None
        self._flow_layouts: list[FlowLayout] = []
        self.current_sort = "date_desc"
        self.delete_mode = False
        self.favorite_mode = False

        scrollbar = self.scroll_area.verticalScrollBar()
        scrollbar.valueChanged.connect(self._on_scroll)
        scrollbar.rangeChanged.connect(self._on_scroll_range_changed)

    def set_records(
        self,
        records: list[dict],
        sort_key: str,
        delete_mode: bool,
        favorite_mode: bool,
        has_active_filter: bool = False,
    ) -> None:
        self.clear()
        self.filtered_records = records
        self.current_sort = sort_key
        self.delete_mode = delete_mode
        self.favorite_mode = favorite_mode
        self.displayed_count = 0
        self.current_group_key = None
        self.current_flow_layout = None

        self._date_group_stats.clear()
        for r in records:
            g_key = (
                r.get("date_key", "")
                if self.current_sort.startswith("date")
                else getattr(self.t, "all_records", "All Records")
            )
            st = self._date_group_stats.setdefault(g_key, {"total": 0, "win": 0, "loss": 0, "mmr": 0})
            st["total"] += 1
            res = r.get("result")
            if res == "win":
                st["win"] += 1
            elif res == "loss":
                st["loss"] += 1
            st["mmr"] += int(r.get("mmr_change") or 0)

        if not self.filtered_records:
            self._render_empty_state(has_active_filter)
            return

        self.load_more_items()

    def _render_empty_state(self, has_active_filter: bool) -> None:
        is_ja = getattr(self.t, "mode_all", "All") == "全て"
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 60, 20, 60)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_label = QLabel("🎬" if not has_active_filter else "🔎")
        icon_label.setStyleSheet("font-size: 40px; background: transparent;")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)

        title_text = (
            ("条件に一致する録画が見つかりません" if is_ja else "No matching recordings found")
            if has_active_filter
            else ("録画データがまだありません" if is_ja else "No recordings yet")
        )
        title_label = QLabel(title_text)
        title_label.setStyleSheet("color: #E2E8F0; font-size: 16px; font-weight: bold; background: transparent;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)

        sub_text = (
            (
                "検索キーワードやフィルター条件を変更してお試しください。"
                if is_ja
                else "Try adjusting your search keywords or filter criteria."
            )
            if has_active_filter
            else (
                "VALORANTで試合を開始すると自動的に録画・保存されます。"
                if is_ja
                else "Matches will be automatically recorded when you play VALORANT."
            )
        )
        sub_label = QLabel(sub_text)
        sub_label.setStyleSheet("color: #788A9C; font-size: 12px; background: transparent;")
        sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(sub_label)

        if has_active_filter and self.on_reset_filters:
            reset_btn = QPushButton("フィルター・検索をリセット" if is_ja else "Reset Filters")
            reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            reset_btn.setFixedWidth(200)
            reset_btn.setStyleSheet("""
                QPushButton {
                    background-color: #FF4655;
                    color: #FFFFFF;
                    font-size: 12px;
                    font-weight: bold;
                    padding: 8px 16px;
                    border-radius: 6px;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #FF5E6B;
                }
            """)
            reset_btn.clicked.connect(self.on_reset_filters)
            layout.addWidget(reset_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self.scroll_layout.addWidget(container)

    def _on_scroll(self, value: int) -> None:
        scrollbar = self.scroll_area.verticalScrollBar()
        if scrollbar.maximum() - value < 300:
            self.load_more_items()

    def _on_scroll_range_changed(self, _min: int, max_val: int) -> None:
        scrollbar = self.scroll_area.verticalScrollBar()
        if max_val - scrollbar.value() < 300 and self.displayed_count < len(self.filtered_records):
            QTimer.singleShot(0, self.check_need_more_items)

    def check_need_more_items(self) -> None:
        if self.displayed_count >= len(self.filtered_records):
            return
        scrollbar = self.scroll_area.verticalScrollBar()
        if scrollbar.maximum() - scrollbar.value() < 300:
            self.load_more_items()

    def load_more_items(self) -> None:
        batch_size = 12 if getattr(self.config, "LIGHTWEIGHT_MODE", False) else 20
        start = self.displayed_count
        end = min(start + batch_size, len(self.filtered_records))
        if start >= end:
            return

        is_ja = getattr(self.t, "mode_all", "All") == "全て"

        for i in range(start, end):
            rec = self.filtered_records[i]
            group_key = (
                rec["date_key"]
                if self.current_sort.startswith("date")
                else getattr(self.t, "all_records", "All Records")
            )

            if self.current_group_key != group_key:
                self.current_group_key = group_key

                date_header_widget = QWidget()
                date_header_widget.setProperty("is_date_header", True)
                date_header_layout = QHBoxLayout(date_header_widget)
                date_header_layout.setContentsMargins(0, 14, 0, 6)
                date_header_layout.setSpacing(10)

                date_checkbox = QPushButton()
                date_checkbox.setCheckable(True)
                date_checkbox.setFixedSize(22, 22)
                date_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
                self._update_date_checkbox_style(
                    date_checkbox, False, "delete" if self.delete_mode else "favorite"
                )
                date_checkbox.setVisible(self.delete_mode or self.favorite_mode)

                accent_bar = QLabel()
                accent_bar.setFixedSize(4, 18)
                accent_bar.setStyleSheet("background-color: #FF4655; border-radius: 2px;")

                date_label = QLabel(group_key)
                date_label.setStyleSheet("font-size: 15px; font-weight: 800; color: #F0F4F8; letter-spacing: 0.5px;")

                date_header_layout.addWidget(date_checkbox)
                date_header_layout.addWidget(accent_bar)
                date_header_layout.addWidget(date_label)

                # 日別サマリーバッジ (試合数・勝敗・MMR収支)
                g_stats = self._date_group_stats.get(group_key, {})
                total_cnt = g_stats.get("total", 0)
                win_cnt = g_stats.get("win", 0)
                loss_cnt = g_stats.get("loss", 0)
                mmr_sum = g_stats.get("mmr", 0)
                if total_cnt > 0:
                    parts = [f"{total_cnt}試合" if is_ja else f"{total_cnt} matches"]
                    if win_cnt or loss_cnt:
                        parts.append(f"{win_cnt}W {loss_cnt}L")
                    if mmr_sum != 0:
                        sign = f"+{mmr_sum}" if mmr_sum > 0 else f"{mmr_sum}"
                        parts.append(f"{sign} RR")
                    summary_badge = QLabel("  •  ".join(parts))
                    summary_badge.setStyleSheet("""
                        QLabel {
                            background-color: rgba(255, 255, 255, 0.06);
                            color: #9FB1C4;
                            font-size: 11px;
                            font-weight: 600;
                            padding: 2px 8px;
                            border-radius: 10px;
                        }
                    """)
                    date_header_layout.addWidget(summary_badge)

                date_header_layout.addStretch()
                self.scroll_layout.addWidget(date_header_widget)

                flow_widget = QWidget()
                self.current_flow_layout = FlowLayout(flow_widget, margin=0, hSpacing=12, vSpacing=12)
                self._flow_layouts.append(self.current_flow_layout)
                date_checkbox.toggled.connect(
                    lambda checked, fw=flow_widget, cb=date_checkbox: self._on_date_checkbox_toggled(
                        checked, fw, cb
                    )
                )
                self.scroll_layout.addWidget(flow_widget)

            item_widget = RecordItemWidget(
                rec["filename"],
                rec["display_name"],
                rec["thumb_path"],
                rec["result"],
                rec["is_favorite"],
                rec.get("mmr_change", 0),
                rec.get("party_members", []),
                rec.get("is_fetching_api", False),
                record_data=rec,
            )
            item_widget.game_mode = rec.get("mode", "Unknown")
            item_widget.doubleClicked.connect(self.on_record_selected)
            item_widget.renameRequested.connect(self.on_rename_requested)
            item_widget.deleteRequested.connect(self.on_delete_requested)
            if self.on_favorite_toggled:
                item_widget.favoriteToggled.connect(self.on_favorite_toggled)
            if self.on_reveal_requested:
                item_widget.revealRequested.connect(self.on_reveal_requested)
            if self.on_selection_changed:
                item_widget.selectionToggled.connect(self.on_selection_changed)
            item_widget.set_selection_mode(
                self.delete_mode or self.favorite_mode,
                "delete" if self.delete_mode else "favorite",
            )
            self.current_flow_layout.addWidget(item_widget)

        self.displayed_count = end
        if self.displayed_count < len(self.filtered_records):
            # 初回描画や大画面環境でスクロールバーが未出現の場合も画面が埋まるまで連続読込する
            QTimer.singleShot(0, self.check_need_more_items)

    def _update_date_checkbox_style(
        self, checkbox: QPushButton, checked: bool, mode: str = "delete"
    ) -> None:
        if checked:
            checkbox.setText("✓")
            color = "#FF4655" if mode == "delete" else "#FFD700"
            text_color = "white" if mode == "delete" else "black"
            checkbox.setStyleSheet(
                f"QPushButton {{ background-color: {color}; color: {text_color}; font-weight: bold; border: 2px solid #FFFFFF; border-radius: 4px; }}"
            )
        else:
            checkbox.setText("")
            checkbox.setStyleSheet(
                "QPushButton { background-color: rgba(15, 25, 35, 0.8); border: 2px solid #8FA3B8; border-radius: 4px; }"
                "QPushButton:hover { border: 2px solid #FFFFFF; }"
            )

    def _on_date_checkbox_toggled(
        self, checked: bool, flow_widget: QWidget, checkbox: QPushButton
    ) -> None:
        mode = "delete" if self.delete_mode else "favorite"
        self._update_date_checkbox_style(checkbox, checked, mode)
        layout = flow_widget.layout()
        if isinstance(layout, FlowLayout):
            for j in range(layout.count()):
                flow_item = layout.itemAt(j)
                if flow_item and flow_item.widget() and isinstance(flow_item.widget(), RecordItemWidget):
                    flow_item.widget().set_checked(checked)
        if self.on_selection_changed:
            self.on_selection_changed()

    def set_all_checked(self, checked: bool) -> None:
        mode = "delete" if self.delete_mode else "favorite"
        for i in range(self.scroll_layout.count()):
            item = self.scroll_layout.itemAt(i)
            widget = item.widget()
            if widget and isinstance(widget, QWidget):
                if widget.property("is_date_header"):
                    layout = widget.layout()
                    cb_item = layout.itemAt(0)
                    if cb_item and cb_item.widget() and isinstance(cb_item.widget(), QPushButton):
                        cb = cb_item.widget()
                        cb.blockSignals(True)
                        cb.setChecked(checked)
                        self._update_date_checkbox_style(cb, checked, mode)
                        cb.blockSignals(False)
                else:
                    layout = widget.layout()
                    if isinstance(layout, FlowLayout):
                        for j in range(layout.count()):
                            flow_item = layout.itemAt(j)
                            if flow_item and flow_item.widget() and isinstance(flow_item.widget(), RecordItemWidget):
                                flow_item.widget().set_checked(checked)
        if self.on_selection_changed:
            self.on_selection_changed()

    def update_selection_mode(self, delete_mode: bool, favorite_mode: bool) -> None:
        self.delete_mode = delete_mode
        self.favorite_mode = favorite_mode
        mode = "delete" if self.delete_mode else "favorite"
        enabled = self.delete_mode or self.favorite_mode
        for i in range(self.scroll_layout.count()):
            item = self.scroll_layout.itemAt(i)
            widget = item.widget()
            if widget and isinstance(widget, QWidget):
                if widget.property("is_date_header"):
                    layout = widget.layout()
                    cb_item = layout.itemAt(0)
                    if cb_item and cb_item.widget() and isinstance(cb_item.widget(), QPushButton):
                        cb = cb_item.widget()
                        cb.setVisible(enabled)
                        if not enabled:
                            cb.blockSignals(True)
                            cb.setChecked(False)
                            self._update_date_checkbox_style(cb, False, mode)
                            cb.blockSignals(False)
                        else:
                            self._update_date_checkbox_style(cb, cb.isChecked(), mode)
                else:
                    layout = widget.layout()
                    if isinstance(layout, FlowLayout):
                        for j in range(layout.count()):
                            flow_item = layout.itemAt(j)
                            if flow_item and flow_item.widget() and isinstance(flow_item.widget(), RecordItemWidget):
                                flow_item.widget().set_selection_mode(enabled, mode)
        if self.on_selection_changed:
            self.on_selection_changed()

    def get_selected_files(self) -> list[str]:
        selected_files = []
        for i in range(self.scroll_layout.count()):
            item = self.scroll_layout.itemAt(i)
            widget = item.widget()
            if widget and isinstance(widget, QWidget):
                layout = widget.layout()
                if isinstance(layout, FlowLayout):
                    for j in range(layout.count()):
                        flow_item = layout.itemAt(j)
                        if flow_item and flow_item.widget() and isinstance(flow_item.widget(), RecordItemWidget):
                            record_widget = flow_item.widget()
                            if record_widget.is_checked():
                                selected_files.append(record_widget.json_filename)
        return selected_files

    def _cleanup_widget(self, widget: QWidget) -> None:
        for child in widget.findChildren(QWidget):
            if hasattr(child, "cleanup"):
                child.cleanup()
        if hasattr(widget, "cleanup"):
            widget.cleanup()
        widget.hide()
        widget.setParent(None)
        widget.deleteLater()

    def clear(self) -> None:
        scrollbar = self.scroll_area.verticalScrollBar()
        scrollbar.blockSignals(True)
        try:
            while self.scroll_layout.count():
                item = self.scroll_layout.takeAt(0)
                widget = item.widget()
                if widget:
                    self._cleanup_widget(widget)
                elif item.layout():
                    self._clear_layout(item.layout())
            self.displayed_count = 0
            self.current_group_key = None
            self.current_flow_layout = None
            self._flow_layouts.clear()
            scrollbar.setValue(0)
        finally:
            scrollbar.blockSignals(False)

    def _clear_layout(self, layout) -> None:
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget:
                    self._cleanup_widget(widget)
                elif item.layout():
                    self._clear_layout(item.layout())
            layout.deleteLater()
