"""Module: @role: 録画一覧の日付グループ化描画、オンデマンド分割読み込み、および選択状態管理を担うレンダラー"""
from typing import Callable, Optional
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
    ) -> None:
        self.config = config
        self.scroll_area = scroll_area
        self.scroll_layout = scroll_layout
        self.t = t
        self.on_record_selected = on_record_selected
        self.on_rename_requested = on_rename_requested
        self.on_delete_requested = on_delete_requested

        self.filtered_records: list[dict] = []
        self.displayed_count = 0
        self.current_group_key: Optional[str] = None
        self.current_flow_layout: Optional[FlowLayout] = None
        self.current_sort = "date_desc"
        self.delete_mode = False
        self.favorite_mode = False

        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def set_records(
        self, records: list[dict], sort_key: str, delete_mode: bool, favorite_mode: bool
    ) -> None:
        self.clear()
        self.filtered_records = records
        self.current_sort = sort_key
        self.delete_mode = delete_mode
        self.favorite_mode = favorite_mode
        self.displayed_count = 0
        self.current_group_key = None
        self.current_flow_layout = None
        self.load_more_items()

    def _on_scroll(self, value: int) -> None:
        scrollbar = self.scroll_area.verticalScrollBar()
        if scrollbar.maximum() - value < 300:
            self.load_more_items()

    def load_more_items(self) -> None:
        batch_size = 12 if getattr(self.config, "LIGHTWEIGHT_MODE", False) else 20
        start = self.displayed_count
        end = min(start + batch_size, len(self.filtered_records))
        if start >= end:
            return

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
                date_header_layout.setContentsMargins(0, 15, 0, 5)

                date_checkbox = QPushButton()
                date_checkbox.setCheckable(True)
                date_checkbox.setFixedSize(20, 20)
                date_checkbox.setStyleSheet(
                    "QPushButton { background-color: rgba(0, 0, 0, 0.6); border: 2px solid #FFFFFF; border-radius: 4px; }"
                )
                date_checkbox.setText("")
                date_checkbox.setVisible(self.delete_mode or self.favorite_mode)

                date_label = QLabel(group_key)
                date_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #FF4655;")

                date_header_layout.addWidget(date_checkbox)
                date_header_layout.addWidget(date_label)
                date_header_layout.addStretch()

                self.scroll_layout.addWidget(date_header_widget)

                flow_widget = QWidget()
                self.current_flow_layout = FlowLayout(flow_widget)
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
            )
            item_widget.game_mode = rec.get("mode", "Unknown")
            item_widget.doubleClicked.connect(self.on_record_selected)
            item_widget.renameRequested.connect(self.on_rename_requested)
            item_widget.deleteRequested.connect(self.on_delete_requested)
            item_widget.set_selection_mode(
                self.delete_mode or self.favorite_mode,
                "delete" if self.delete_mode else "favorite",
            )
            self.current_flow_layout.addWidget(item_widget)

        self.displayed_count = end

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
                "QPushButton { background-color: rgba(0, 0, 0, 0.6); border: 2px solid #FFFFFF; border-radius: 4px; }"
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

    def clear(self) -> None:
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())
        self.displayed_count = 0
        self.current_group_key = None
        self.current_flow_layout = None

    def _clear_layout(self, layout) -> None:
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
                elif item.layout():
                    self._clear_layout(item.layout())
            layout.deleteLater()
