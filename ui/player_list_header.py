"""Module: @role: 録画一覧画面の上部ナビゲーション（ゲームモードタブ・検索バー・一括選択トグル・設定ボタン）を担うヘッダー"""
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLineEdit
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QByteArray
from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer

SETTINGS_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M19.14,12.94c0.04-0.3,0.06-0.61,0.06-0.94c0-0.32-0.02-0.64-0.06-0.94l2.03-1.58c0.18-0.14,0.23-0.41,0.12-0.61 l-1.92-3.32c-0.12-0.22-0.37-0.29-0.59-0.22l-2.39,0.96c-0.5-0.38-1.03-0.7-1.62-0.94L14.4,2.81c-0.04-0.24-0.24-0.41-0.48-0.41 h-3.84c-0.24,0-0.43,0.17-0.47,0.41L9.25,5.35C8.66,5.59,8.12,5.92,7.63,6.29L5.24,5.33c-0.22-0.08-0.47,0-0.59,0.22L2.73,8.87 C2.62,9.08,2.66,9.34,2.86,9.48l2.03,1.58C4.84,11.36,4.8,11.69,4.8,12s0.02,0.64,0.06,0.94l-2.03,1.58 c-0.18,0.14-0.23,0.41-0.12,0.61l1.92,3.32c0.12,0.22,0.37,0.29,0.59,0.22l2.39-0.96c0.5,0.38,1.03,0.7,1.62-0.94l0.36,2.54 c0.05,0.24,0.24,0.41,0.48,0.41h3.84c0.24,0,0.43-0.17,0.47-0.41l0.36-2.54c0.59-0.24,1.13-0.56,1.62-0.94l2.39,0.96 c0.22,0.08,0.47,0,0.59-0.22l1.92-3.32c0.12,0.22,0.07-0.49-0.12-0.61L19.14,12.94z M12,15.6c-1.98,0-3.6-1.62-3.6-3.6 s1.62-3.6,3.6-3.6s3.6,1.62,3.6,3.6S13.98,15.6,12,15.6z"/>
</svg>"""

TRASH_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M16 9v10H8V9h8m-1.5-6h-5l-1 1H5v2h14V4h-3.5l-1-1zM18 7H6v12c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7z"/>
</svg>"""

STAR_OUTLINE_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="transparent" stroke="white" stroke-width="2">
  <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
</svg>"""


def _svg_icon(svg_bytes: bytes, size: int = 24) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class PlayerListHeader(QWidget):
    tabChanged = pyqtSignal(str)
    deleteModeToggled = pyqtSignal(bool)
    favoriteModeToggled = pyqtSignal(bool)
    settingsRequested = pyqtSignal()
    searchChanged = pyqtSignal(str)

    def __init__(self, t, parent=None):
        super().__init__(parent)
        self.t = t
        self.setStyleSheet("background-color: transparent; border: none;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(6)

        self.tab_buttons = {}
        modes = [
            ("All", getattr(self.t, "mode_all", "全て")),
            ("Competitive", getattr(self.t, "mode_competitive", "コンペティティブ")),
            ("Unrated", getattr(self.t, "mode_unrated", "アンレート")),
            ("Deathmatch", getattr(self.t, "mode_deathmatch", "デスマッチ")),
            ("Swiftplay", getattr(self.t, "mode_swiftplay", "スイフト")),
        ]
        self.current_mode = "All"

        for mode_key, mode_name in modes:
            btn = QPushButton(mode_name)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, m=mode_key: self._on_tab_clicked(m))
            layout.addWidget(btn)
            self.tab_buttons[mode_key] = btn

        self._update_tab_styles()

        layout.addStretch()

        # キーワード検索ボックス
        self.search_input = QLineEdit()
        is_ja = getattr(self.t, "mode_all", "All") == "全て"
        self.search_input.setPlaceholderText(
            "🔍 マップ・エージェント・名前で検索..." if is_ja else "🔍 Search map, agent, title..."
        )
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedWidth(240)
        self.search_input.setFixedHeight(32)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #15202B;
                color: #F0F4F8;
                border: 1px solid #2A3A4B;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border: 1px solid #FF4655;
                background-color: #192734;
            }
        """)
        self.search_input.textChanged.connect(self.searchChanged.emit)
        layout.addWidget(self.search_input)

        # 一括お気に入り選択ボタン
        self.fav_btn = QPushButton()
        self.fav_btn.setFixedSize(34, 34)
        self.fav_btn.setCheckable(True)
        self.fav_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fav_btn.setToolTip("一括お気に入り選択モード" if is_ja else "Batch Favorite Mode")
        self.fav_btn.setStyleSheet("""
            QPushButton {
                background-color: #15202B;
                border: 1px solid #2A3A4B;
                border-radius: 6px;
            }
            QPushButton:checked {
                background-color: rgba(255, 215, 0, 0.35);
                border: 1px solid #FFD700;
            }
            QPushButton:hover {
                background-color: #203040;
                border: 1px solid #5A7088;
            }
        """)
        self.fav_btn.setIcon(_svg_icon(STAR_OUTLINE_SVG))
        self.fav_btn.setIconSize(QSize(18, 18))
        self.fav_btn.clicked.connect(self.favoriteModeToggled.emit)
        layout.addWidget(self.fav_btn)

        # 一括削除モードボタン
        self.trash_btn = QPushButton()
        self.trash_btn.setFixedSize(34, 34)
        self.trash_btn.setCheckable(True)
        self.trash_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.trash_btn.setToolTip("一括削除選択モード" if is_ja else "Batch Delete Mode")
        self.trash_btn.setStyleSheet("""
            QPushButton {
                background-color: #15202B;
                border: 1px solid #2A3A4B;
                border-radius: 6px;
            }
            QPushButton:checked {
                background-color: rgba(255, 70, 85, 0.45);
                border: 1px solid #FF4655;
            }
            QPushButton:hover {
                background-color: #203040;
                border: 1px solid #5A7088;
            }
        """)
        self.trash_btn.setIcon(_svg_icon(TRASH_SVG))
        self.trash_btn.setIconSize(QSize(18, 18))
        self.trash_btn.clicked.connect(self.deleteModeToggled.emit)
        layout.addWidget(self.trash_btn)

        # 設定ボタン
        settings_btn = QPushButton()
        settings_btn.setFixedSize(34, 34)
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setToolTip(getattr(self.t, "settings_title", "Settings"))
        settings_btn.setStyleSheet("""
            QPushButton {
                background-color: #15202B;
                border: 1px solid #2A3A4B;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #203040;
                border: 1px solid #5A7088;
            }
        """)
        settings_btn.setIcon(_svg_icon(SETTINGS_SVG))
        settings_btn.setIconSize(QSize(18, 18))
        settings_btn.clicked.connect(self.settingsRequested)
        layout.addWidget(settings_btn)

    def _update_tab_styles(self):
        for mode, btn in self.tab_buttons.items():
            if mode == self.current_mode:
                btn.setChecked(True)
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(255, 70, 85, 0.14);
                        color: #FFFFFF;
                        border: none;
                        border-bottom: 3px solid #FF4655;
                        border-top-left-radius: 6px;
                        border-top-right-radius: 6px;
                        padding: 8px 16px;
                        font-weight: bold;
                        font-size: 13px;
                    }
                """)
            else:
                btn.setChecked(False)
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: transparent;
                        color: #8FA3B8;
                        border: none;
                        border-bottom: 3px solid transparent;
                        padding: 8px 16px;
                        font-size: 13px;
                    }
                    QPushButton:hover {
                        color: #FFFFFF;
                        background-color: rgba(255, 255, 255, 0.04);
                        border-bottom: 3px solid #455A64;
                    }
                """)

    def _on_tab_clicked(self, mode):
        self.current_mode = mode
        self._update_tab_styles()
        self.tabChanged.emit(mode)

    def set_delete_mode(self, checked):
        self.trash_btn.setChecked(checked)

    def set_favorite_mode(self, checked):
        self.fav_btn.setChecked(checked)
