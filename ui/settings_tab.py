"""Module: @role: 設定画面のタブ構成・スクロール領域管理・音声モニター稼働制御を担うモジュール"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from core.config import Config
from ui.settings_general import GeneralSettingsWidget
from ui.settings_playback import PlaybackSettingsWidget
from ui.settings_record import RecordSettingsWidget


class SettingsTab(QWidget):
    backRequested = pyqtSignal()

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        from core.i18n import get_trans
        self.t = get_trans(self.config.LANGUAGE)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(28, 22, 28, 22)
        main_layout.setSpacing(14)

        header_layout = QHBoxLayout()
        title = QLabel(self.t.settings_title)
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #FF4655; padding: 2px 0;")

        back_btn = QPushButton(self.t.back_to_recordings)
        back_btn.setMinimumWidth(190)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(self.backRequested)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(back_btn)

        main_layout.addLayout(header_layout)

        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #2E3842;
                border-radius: 6px;
                background: #141E28;
            }
            QTabBar::tab {
                background: #1F2731;
                color: #B0B8C0;
                padding: 10px 26px;
                min-height: 22px;
                font-size: 14px;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
                margin-right: 4px;
            }
            QTabBar::tab:selected {
                background: #FF4655;
                color: #FFFFFF;
                font-weight: bold;
            }
            QTabBar::tab:hover:!selected {
                background: #2A3441;
                color: #FFFFFF;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
        """)

        self.general_widget = GeneralSettingsWidget(self.config, self.t, self)
        self.record_widget = RecordSettingsWidget(self.config, self.t, self)
        self.playback_widget = PlaybackSettingsWidget(self.config, self.t, self)

        self.general_scroll = self._wrap_in_scroll_area(self.general_widget)
        self.record_scroll = self._wrap_in_scroll_area(self.record_widget)
        self.playback_scroll = self._wrap_in_scroll_area(self.playback_widget)

        self.tab_widget.addTab(self.general_scroll, self.t.tab_general)
        self.tab_widget.addTab(self.record_scroll, self.t.tab_record)
        self.tab_widget.addTab(self.playback_scroll, self.t.tab_playback)
        self.tab_widget.currentChanged.connect(self._on_subtab_changed)

        main_layout.addWidget(self.tab_widget)
        self.setLayout(main_layout)

    def _wrap_in_scroll_area(self, content_widget: QWidget) -> QScrollArea:
        # ウィンドウ縮小時に内部ウィジェットが圧縮されて文字が見切れるのを防ぐ
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content_widget.setStyleSheet("""
            QWidget {
                background-color: #141E28;
            }
            QGroupBox {
                background-color: #182430;
                border: 1px solid #2E3A46;
                border-radius: 6px;
                margin-top: 16px;
                padding: 18px 16px 14px 16px;
                font-size: 14px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 14px;
                padding: 2px 10px;
                background-color: #182430;
                color: #FF4655;
                border-radius: 4px;
            }
            QLabel {
                background-color: transparent;
                color: #ECE8E1;
                font-size: 14px;
                min-height: 22px;
            }
            QCheckBox {
                background-color: transparent;
                font-size: 14px;
                min-height: 26px;
            }
            QLineEdit, QComboBox, QSpinBox {
                background-color: #0F1923;
                border: 1px solid #3A4652;
                border-radius: 4px;
                padding: 6px 10px;
                min-height: 24px;
                font-size: 14px;
                color: #ECE8E1;
            }
            QComboBox {
                padding-right: 34px;
            }
            QLineEdit:hover, QComboBox:hover, QSpinBox:hover {
                border-color: #556677;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 28px;
                border-left: 1px solid #3A4652;
                background-color: #182430;
                border-top-right-radius: 3px;
                border-bottom-right-radius: 3px;
            }
            QComboBox::drop-down:hover {
                background-color: #223242;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0px;
                height: 0px;
            }
            QComboBox QAbstractItemView {
                background-color: #0F1923;
                color: #ECE8E1;
                border: 1px solid #556677;
                selection-background-color: #FF4655;
                selection-color: #FFFFFF;
                outline: none;
            }
        """)
        scroll.setWidget(content_widget)
        return scroll

    def _on_subtab_changed(self, index: int) -> None:
        # 録画設定タブ表示中かつ非録画時のみ音声モニターを稼働させリソース競合と負荷を防止
        current = self.tab_widget.currentWidget()
        is_record_tab = current in (self.record_scroll, self.record_widget)
        if self.isVisible() and is_record_tab:
            self.record_widget.refresh_from_config()
            main_win = self.window()
            is_rec = getattr(main_win, "rec_button", None) and main_win.rec_button.isChecked()
            if not is_rec:
                self.record_widget.start_monitors()
        else:
            self.record_widget.stop_monitors()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._on_subtab_changed(self.tab_widget.currentIndex())

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.record_widget.stop_monitors()
