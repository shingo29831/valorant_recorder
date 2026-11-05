"""Module: @role: 初回起動時の録画仕様（自動録画と手動フォールバック）を案内するチュートリアルダイアログ"""
from typing import Optional
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QFrame, QWidget
)
from PyQt6.QtCore import Qt
from core.config import Config
from core.i18n import Translations

class TutorialDialog(QDialog):
    def __init__(self, config: Config, t: Translations, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        self.t = t
        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle(getattr(self.t, 'tutorial_title', "ValoReco の使い方"))
        self.setFixedSize(580, 500)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.MSWindowsFixedSizeDialogHint)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        # ヘッダー部
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)
        title_label = QLabel(getattr(self.t, 'tutorial_welcome', "ValoReco へようこそ！"))
        title_label.setObjectName("tutorialHeaderTitle")
        subtitle_label = QLabel(getattr(self.t, 'tutorial_title', "ValoReco の使い方"))
        subtitle_label.setObjectName("tutorialHeaderSubtitle")
        header_layout.addWidget(title_label)
        header_layout.addWidget(subtitle_label)
        layout.addLayout(header_layout)

        # 自動録画カード
        card_auto = self._create_card(
            badge_text="AUTO",
            badge_class="card-badge-auto",
            title=getattr(self.t, 'tutorial_auto_title', "1. 試合の自動録画"),
            desc=getattr(self.t, 'tutorial_auto_desc', "VALORANT を起動して試合を開始すると、自動的に録画が開始されます。")
        )
        layout.addWidget(card_auto)

        # 手動録画フォールバックカード
        card_manual = self._create_card(
            badge_text="MANUAL",
            badge_class="card-badge-manual",
            title=getattr(self.t, 'tutorial_manual_title', "2. 手動での録画起動と停止"),
            desc=getattr(self.t, 'tutorial_manual_desc', "万が一正常に起動しなかった場合は、画面右下のボタンから手動で起動・停止が可能です。")
        )
        layout.addWidget(card_manual)

        layout.addStretch()

        # フッター操作部
        footer_layout = QHBoxLayout()
        self.dont_show_cb = QCheckBox(getattr(self.t, 'tutorial_dont_show_again', "次回から表示しない"))
        self.dont_show_cb.setChecked(True)
        footer_layout.addWidget(self.dont_show_cb)
        footer_layout.addStretch()

        start_btn = QPushButton(getattr(self.t, 'tutorial_start_btn', "使い方を理解して始める"))
        start_btn.setObjectName("tutorialStartBtn")
        start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        start_btn.clicked.connect(self._on_start_clicked)
        footer_layout.addWidget(start_btn)
        layout.addLayout(footer_layout)

        self._apply_styles()

    def _create_card(self, badge_text: str, badge_class: str, title: str, desc: str) -> QFrame:
        card = QFrame()
        card.setProperty("class", "tutorial-card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        badge = QLabel(badge_text)
        badge.setProperty("class", badge_class)
        title_row.addWidget(badge)

        lbl_title = QLabel(title)
        lbl_title.setObjectName("cardTitle")
        title_row.addWidget(lbl_title)
        title_row.addStretch()
        card_layout.addLayout(title_row)

        lbl_desc = QLabel(desc)
        lbl_desc.setObjectName("cardDesc")
        lbl_desc.setWordWrap(True)
        card_layout.addWidget(lbl_desc)
        return card

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background-color: #0F1923;
                color: #ECE8E1;
            }
            QLabel#tutorialHeaderTitle {
                color: #ECE8E1;
                font-size: 20px;
                font-weight: bold;
            }
            QLabel#tutorialHeaderSubtitle {
                color: #8B98A5;
                font-size: 13px;
            }
            QFrame[class="tutorial-card"] {
                background-color: #1A242D;
                border: 1px solid #2C3E50;
                border-radius: 6px;
            }
            QLabel[class="card-badge-auto"] {
                background-color: #0F82FF;
                color: #FFFFFF;
                font-size: 10px;
                font-weight: bold;
                padding: 3px 6px;
                border-radius: 3px;
            }
            QLabel[class="card-badge-manual"] {
                background-color: #FF4655;
                color: #FFFFFF;
                font-size: 10px;
                font-weight: bold;
                padding: 3px 6px;
                border-radius: 3px;
            }
            QLabel#cardTitle {
                color: #FFFFFF;
                font-size: 14px;
                font-weight: bold;
            }
            QLabel#cardDesc {
                color: #BAC7D5;
                font-size: 12px;
                line-height: 1.4;
            }
            QCheckBox {
                color: #8B98A5;
                font-size: 12px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #3A4F66;
                border-radius: 3px;
                background-color: #151F28;
            }
            QCheckBox::indicator:checked {
                background-color: #FF4655;
                border-color: #FF4655;
            }
            QPushButton#tutorialStartBtn {
                background-color: #FF4655;
                color: #FFFFFF;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 20px;
                border: none;
                border-radius: 4px;
            }
            QPushButton#tutorialStartBtn:hover {
                background-color: #E03E4C;
            }
            QPushButton#tutorialStartBtn:pressed {
                background-color: #C03542;
            }
        """)

    def _on_start_clicked(self) -> None:
        if self.dont_show_cb.isChecked():
            self.config.TUTORIAL_COMPLETED = True
            self.config.save()
        self.accept()
