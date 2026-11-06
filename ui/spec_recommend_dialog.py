"""Module: @role: 初回起動時やユーザー要求時にPCスペックを診断し、オススメの録画設定を表示・適用するダイアログ"""
from typing import Optional
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QWidget, QGridLayout
)
from PyQt6.QtCore import Qt
from core.config import Config
from core.i18n import Translations
from core.spec_detector import detect_specs, evaluate_recommended_settings, SpecInfo, RecommendedSettings

class SpecRecommendDialog(QDialog):
    def __init__(self, config: Config, t: Translations, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        self.t = t
        self.specs: SpecInfo = detect_specs()
        self.recommended: RecommendedSettings = evaluate_recommended_settings(
            self.specs,
            getattr(self.config, 'LANGUAGE', 'ja')
        )
        self._init_ui()

    def _init_ui(self) -> None:
        title = getattr(self.t, 'spec_dialog_title', "スペック診断とオススメ設定")
        self.setWindowTitle(title)
        self.setFixedSize(620, 600)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.MSWindowsFixedSizeDialogHint)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(14)

        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("specHeaderTitle")
        subtitle_lbl = QLabel(getattr(self.t, 'spec_dialog_subtitle', "お使いのPCスペックに最適な録画設定を検出しました"))
        subtitle_lbl.setObjectName("specHeaderSubtitle")
        header_layout.addWidget(title_lbl)
        header_layout.addWidget(subtitle_lbl)
        layout.addLayout(header_layout)

        card_specs = self._create_spec_card()
        layout.addWidget(card_specs)

        card_recommended = self._create_recommended_card()
        layout.addWidget(card_recommended)

        layout.addStretch()

        footer_layout = QHBoxLayout()
        footer_layout.setSpacing(12)

        skip_btn = QPushButton(getattr(self.t, 'spec_skip_btn', "現在の設定を維持する"))
        skip_btn.setObjectName("specSkipBtn")
        skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        skip_btn.clicked.connect(self._on_skip_clicked)
        footer_layout.addWidget(skip_btn)

        footer_layout.addStretch()

        apply_btn = QPushButton(getattr(self.t, 'spec_apply_btn', "オススメ設定を適用して開始"))
        apply_btn.setObjectName("specApplyBtn")
        apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_btn.clicked.connect(self._on_apply_clicked)
        footer_layout.addWidget(apply_btn)

        layout.addLayout(footer_layout)

        self._apply_styles()

    def _create_spec_card(self) -> QFrame:
        card = QFrame()
        card.setProperty("class", "spec-card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(8)

        card_title = QLabel(getattr(self.t, 'spec_hardware_title', "検出されたシステム環境"))
        card_title.setObjectName("cardSectionTitle")
        card_layout.addWidget(card_title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)

        cpu_label = getattr(self.t, 'spec_cpu', "CPU")
        ram_label = getattr(self.t, 'spec_memory', "メモリ")
        gpu_label = getattr(self.t, 'spec_gpu', "GPU")
        res_label = getattr(self.t, 'spec_resolution', "解像度")

        cpu_val = f"{self.specs.cpu_name} ({self.specs.cpu_cores} Threads)"
        ram_val = f"{self.specs.ram_gb:.1f} GB"
        gpu_val = ", ".join(self.specs.gpu_names) if self.specs.gpu_names else "Unknown"
        res_val = f"{self.specs.screen_width} x {self.specs.screen_height}"

        items = [
            (cpu_label, cpu_val),
            (ram_label, ram_val),
            (gpu_label, gpu_val),
            (res_label, res_val),
        ]

        for row, (k, v) in enumerate(items):
            lbl_k = QLabel(k)
            lbl_k.setProperty("class", "spec-key")
            lbl_v = QLabel(v)
            lbl_v.setProperty("class", "spec-val")
            lbl_v.setWordWrap(True)
            grid.addWidget(lbl_k, row, 0)
            grid.addWidget(lbl_v, row, 1)

        card_layout.addLayout(grid)
        return card

    def _create_recommended_card(self) -> QFrame:
        card = QFrame()
        card.setProperty("class", "spec-card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        card_title = QLabel(getattr(self.t, 'spec_recommended_title', "オススメの録画設定"))
        card_title.setObjectName("cardSectionTitle")
        header_row.addWidget(card_title)

        badge = QLabel(self.recommended.preset_title)
        badge_cls = f"badge-{self.recommended.preset_type}"
        badge.setProperty("class", badge_cls)
        header_row.addWidget(badge)
        header_row.addStretch()
        card_layout.addLayout(header_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)

        enc_display = self.recommended.encoder
        if "nvenc" in enc_display:
            enc_display += " (NVIDIA GPU加速)"
        elif "amf" in enc_display:
            enc_display += " (AMD GPU加速)"
        elif "qsv" in enc_display:
            enc_display += " (Intel GPU加速)"
        elif "libx264" in enc_display:
            enc_display += " (CPU ソフトウェア)"

        is_ja = getattr(self.config, 'LANGUAGE', 'ja') == 'ja'
        lw_str = ("オン (低負荷)" if self.recommended.lightweight_mode else "オフ (高画質)") if is_ja else ("ON (Low Load)" if self.recommended.lightweight_mode else "OFF (High Quality)")

        settings_items = [
            (getattr(self.t, 'encoder', "エンコーダ"), enc_display),
            (getattr(self.t, 'resolution', "解像度"), self.recommended.resolution),
            (getattr(self.t, 'recording_fps', "フレームレート"), f"{self.recommended.fps} FPS"),
            (getattr(self.t, 'lightweight_mode', "軽量モード"), lw_str),
        ]

        for row, (k, v) in enumerate(settings_items):
            lbl_k = QLabel(k)
            lbl_k.setProperty("class", "spec-key")
            lbl_v = QLabel(v)
            lbl_v.setProperty("class", "spec-val-highlight")
            grid.addWidget(lbl_k, row, 0)
            grid.addWidget(lbl_v, row, 1)

        card_layout.addLayout(grid)

        reason_frame = QFrame()
        reason_frame.setProperty("class", "reason-frame")
        reason_layout = QVBoxLayout(reason_frame)
        reason_layout.setContentsMargins(10, 8, 10, 8)
        reason_lbl = QLabel(self.recommended.reason_text)
        reason_lbl.setObjectName("reasonLabel")
        reason_lbl.setWordWrap(True)
        reason_layout.addWidget(reason_lbl)
        card_layout.addWidget(reason_frame)

        return card

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background-color: #0F1923;
                color: #ECE8E1;
            }
            QLabel#specHeaderTitle {
                color: #ECE8E1;
                font-size: 20px;
                font-weight: bold;
            }
            QLabel#specHeaderSubtitle {
                color: #8B98A5;
                font-size: 13px;
            }
            QFrame[class="spec-card"] {
                background-color: #1A242D;
                border: 1px solid #2C3E50;
                border-radius: 6px;
            }
            QLabel#cardSectionTitle {
                color: #FFFFFF;
                font-size: 14px;
                font-weight: bold;
            }
            QLabel[class="spec-key"] {
                color: #8B98A5;
                font-size: 12px;
                min-width: 90px;
            }
            QLabel[class="spec-val"] {
                color: #ECE8E1;
                font-size: 12px;
            }
            QLabel[class="spec-val-highlight"] {
                color: #00DF81;
                font-size: 12px;
                font-weight: bold;
            }
            QLabel[class="badge-high"] {
                background-color: #0A382A;
                color: #00DF81;
                font-size: 10px;
                font-weight: bold;
                padding: 3px 8px;
                border-radius: 3px;
                border: 1px solid #00DF81;
            }
            QLabel[class="badge-balanced"] {
                background-color: #10355A;
                color: #0F82FF;
                font-size: 10px;
                font-weight: bold;
                padding: 3px 8px;
                border-radius: 3px;
                border: 1px solid #0F82FF;
            }
            QLabel[class="badge-lightweight"] {
                background-color: #4A3510;
                color: #FFA500;
                font-size: 10px;
                font-weight: bold;
                padding: 3px 8px;
                border-radius: 3px;
                border: 1px solid #FFA500;
            }
            QFrame[class="reason-frame"] {
                background-color: #151F28;
                border: 1px dashed #2C3E50;
                border-radius: 4px;
            }
            QLabel#reasonLabel {
                color: #BAC7D5;
                font-size: 12px;
                line-height: 1.4;
            }
            QPushButton#specSkipBtn {
                background-color: #2C3E50;
                color: #BAC7D5;
                font-size: 13px;
                padding: 8px 16px;
                border: none;
                border-radius: 4px;
            }
            QPushButton#specSkipBtn:hover {
                background-color: #3A4F66;
                color: #FFFFFF;
            }
            QPushButton#specApplyBtn {
                background-color: #FF4655;
                color: #FFFFFF;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 20px;
                border: none;
                border-radius: 4px;
            }
            QPushButton#specApplyBtn:hover {
                background-color: #E03E4C;
            }
            QPushButton#specApplyBtn:pressed {
                background-color: #C03542;
            }
        """)

    def _on_skip_clicked(self) -> None:
        self.config.INITIAL_SETUP_COMPLETED = True
        self.config.save()
        self.accept()

    def _on_apply_clicked(self) -> None:
        self.config.RECORD_ENCODER = self.recommended.encoder
        self.config.RECORD_RESOLUTION = self.recommended.resolution
        self.config.RECORD_FPS = self.recommended.fps
        self.config.LIGHTWEIGHT_MODE = self.recommended.lightweight_mode
        self.config.INITIAL_SETUP_COMPLETED = True
        self.config.save()
        self.accept()
