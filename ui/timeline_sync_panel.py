"""Module: @role: タイムラインと映像のズレを手動微調整・イベント基準合わせ・自動同期するUIパネル"""
from typing import Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget


class TimelineSyncPanel(QWidget):
    offsetDeltaRequested = pyqtSignal(int)
    alignEventRequested = pyqtSignal(int)
    autoSyncRequested = pyqtSignal()
    resetRequested = pyqtSignal()
    closeRequested = pyqtSignal()

    def __init__(self, t: object, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.t = t
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet("""
            TimelineSyncPanel {
                background-color: #16222E;
                border: 1px solid #2A3A4A;
                border-radius: 6px;
            }
            QPushButton {
                background-color: #243444;
                color: #ECE8E1;
                border: 1px solid #35495E;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #31475C;
            }
            QPushButton:disabled {
                background-color: #1B2733;
                color: #6B7C8E;
            }
            QComboBox {
                background-color: #1B2836;
                color: #ECE8E1;
                border: 1px solid #35495E;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 12px;
                min-width: 170px;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)

        self.offset_label = QLabel()
        self.offset_label.setStyleSheet("color: #ECE8E1; font-weight: bold; font-size: 12px;")
        layout.addWidget(self.offset_label)

        for label, delta_ms in (("-10s", -10000), ("-1s", -1000), ("-0.5s", -500), ("+0.5s", 500), ("+1s", 1000), ("+10s", 10000)):
            btn = QPushButton(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, d=delta_ms: self.offsetDeltaRequested.emit(d))
            layout.addWidget(btn)

        layout.addSpacing(6)

        self.event_combo = QComboBox()
        self.event_combo.setMaxVisibleItems(12)
        layout.addWidget(self.event_combo)

        self.align_btn = QPushButton(getattr(self.t, "sync_align_here", "現在位置に合わせる"))
        self.align_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.align_btn.clicked.connect(self._on_align_clicked)
        layout.addWidget(self.align_btn)

        self.auto_sync_btn = QPushButton(getattr(self.t, "sync_auto_btn", "映像から自動同期"))
        self.auto_sync_btn.setStyleSheet(
            "QPushButton { background-color: #0078D4; border-color: #0086F0; font-weight: bold; }"
            "QPushButton:hover { background-color: #1084E0; }"
        )
        self.auto_sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_sync_btn.clicked.connect(self.autoSyncRequested.emit)
        layout.addWidget(self.auto_sync_btn)

        self.reset_btn = QPushButton(getattr(self.t, "sync_reset", "リセット"))
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.clicked.connect(self.resetRequested.emit)
        layout.addWidget(self.reset_btn)

        layout.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.closeRequested.emit)
        layout.addWidget(close_btn)

        self.update_offset_display(0, "")

    def _on_align_clicked(self) -> None:
        base_time_ms = self.event_combo.currentData()
        if isinstance(base_time_ms, int):
            self.alignEventRequested.emit(base_time_ms)

    def update_offset_display(self, offset_ms: int, source: str = "") -> None:
        sec = offset_ms / 1000.0
        sign = "+" if sec >= 0 else ""
        src_suffix = ""
        if source == "auto":
            src_suffix = f" ({getattr(self.t, 'sync_source_auto', '自動')})"
        elif source == "manual":
            src_suffix = f" ({getattr(self.t, 'sync_source_manual', '手動')})"
        prefix = getattr(self.t, "sync_offset_label", "ズレ補正:")
        self.offset_label.setText(f"{prefix} {sign}{sec:.1f}s{src_suffix}")

    def set_events(self, events: list[dict], format_time_fn) -> None:
        self.event_combo.clear()
        for ev in events:
            label = ev.get("label") or format_time_fn(max(0, int(ev.get("base_time_ms", 0))))
            self.event_combo.addItem(label, int(ev.get("base_time_ms", 0)))
        has_events = self.event_combo.count() > 0
        self.event_combo.setEnabled(has_events)
        self.align_btn.setEnabled(has_events)

    def set_auto_sync_busy(self, busy: bool) -> None:
        self.auto_sync_btn.setEnabled(not busy)
        if busy:
            self.auto_sync_btn.setText(getattr(self.t, "sync_auto_running", "解析中..."))
        else:
            self.auto_sync_btn.setText(getattr(self.t, "sync_auto_btn", "映像から自動同期"))
