"""Module: @role: 録画映像設定（FPS・エンコーダ・解像度・軽量モード）のUI管理と音声設定ウィジェットの統合を担うモジュール"""
import os
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded
from recorder.ffmpeg_recorder import get_available_encoders
from ui.settings_audio import AudioSettingsWidget
from ui.settings_utils import HoverWheelComboBox


class RecordSettingsWidget(QWidget):
    def __init__(self, config, t, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.t = t

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(16, 12, 16, 16)
        main_layout.setSpacing(18)

        video_group = QGroupBox(self.t.video_settings)
        video_layout = QFormLayout()
        video_layout.setContentsMargins(16, 18, 16, 14)
        video_layout.setHorizontalSpacing(20)
        video_layout.setVerticalSpacing(14)
        video_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        video_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.spec_diag_btn = QPushButton("🔍 " + getattr(self.t, "run_spec_diag", "PCスペックに合わせてオススメ設定を診断"))
        self.spec_diag_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.spec_diag_btn.setStyleSheet("""
            QPushButton {
                background-color: #102A43;
                color: #48D1CC;
                border: 1px solid #2680C2;
                border-radius: 5px;
                padding: 8px 16px;
                min-height: 24px;
                font-weight: bold;
                font-size: 13px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #1A365D;
                color: #FFFFFF;
                border-color: #48D1CC;
            }
            QPushButton:pressed {
                background-color: #0B1D33;
            }
        """)
        self.spec_diag_btn.clicked.connect(self._open_spec_dialog)
        video_layout.addRow("", self.spec_diag_btn)

        self.fps_input = HoverWheelComboBox()
        self.fps_input.setCursor(Qt.CursorShape.PointingHandCursor)
        fps_items = ["30", "60", "120", "144"]
        cur_fps = str(getattr(self.config, "RECORD_FPS", "60"))
        if cur_fps and cur_fps not in fps_items:
            fps_items.append(cur_fps)
        self.fps_input.addItems(fps_items)
        self.fps_input.setCurrentText(cur_fps if cur_fps in fps_items else "60")
        self.fps_input.currentTextChanged.connect(self._save_settings)

        app_data_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ValoReco")
        os.makedirs(app_data_dir, exist_ok=True)
        ffmpeg_path = ensure_ffmpeg_downloaded(app_data_dir)
        available_encoders, encoder_warning_keys = get_available_encoders(ffmpeg_path)

        self.encoder_input = HoverWheelComboBox()
        self.encoder_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self.encoder_input.addItems(available_encoders)
        current_enc = self.config.RECORD_ENCODER
        if current_enc in available_encoders:
            self.encoder_input.setCurrentText(current_enc)
        else:
            self.encoder_input.setCurrentText(available_encoders[0])
            self.config.RECORD_ENCODER = available_encoders[0]
            self.config.save()

        self.encoder_input.currentTextChanged.connect(self._save_settings)

        encoder_layout = QVBoxLayout()
        encoder_layout.setContentsMargins(0, 0, 0, 0)
        encoder_layout.setSpacing(6)
        encoder_layout.addWidget(self.encoder_input)
        if encoder_warning_keys:
            warning_texts = [getattr(self.t, key, key) for key in encoder_warning_keys]
            warning_label = QLabel("\n".join(warning_texts))
            warning_label.setWordWrap(True)
            warning_label.setStyleSheet("color: #FFB400; font-size: 13px; font-weight: bold; padding: 2px 0;")
            encoder_layout.addWidget(warning_label)

        self.res_input = HoverWheelComboBox()
        self.res_input.setCursor(Qt.CursorShape.PointingHandCursor)
        res_items = ["2560x1440", "1920x1080", "1600x900", "1280x720"]
        cur_res = str(getattr(self.config, "RECORD_RESOLUTION", "1920x1080"))
        if cur_res and "x" in cur_res.lower() and cur_res not in res_items:
            res_items.append(cur_res)
        self.res_input.addItems(res_items)
        if cur_res in res_items:
            self.res_input.setCurrentText(cur_res)
        else:
            self.res_input.setCurrentText("1920x1080")
        self.res_input.currentTextChanged.connect(self._save_settings)

        self.video_format_input = HoverWheelComboBox()
        self.video_format_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self.video_format_input.addItem("ddagrab (GPU Direct)", "ddagrab")
        self.video_format_input.addItem("gdigrab (Compatibility)", "gdigrab")
        cur_vfmt = str(getattr(self.config, "RECORD_VIDEO_FORMAT", "ddagrab")).strip().lower()
        vfmt_idx = self.video_format_input.findData(cur_vfmt if cur_vfmt in ("ddagrab", "gdigrab") else "ddagrab")
        if vfmt_idx >= 0:
            self.video_format_input.setCurrentIndex(vfmt_idx)
        self.video_format_input.currentIndexChanged.connect(self._save_settings)

        video_layout.addRow(self.t.recording_fps, self.fps_input)
        video_layout.addRow(self.t.encoder, encoder_layout)
        video_layout.addRow(self.t.resolution, self.res_input)
        video_layout.addRow(getattr(self.t, "video_format", "Capture Mode:"), self.video_format_input)

        self.lightweight_mode_cb = QCheckBox(
            getattr(self.t, "lightweight_mode", "軽量モードを有効にする (低負荷録画・省メモリ動作)")
        )
        self.lightweight_mode_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lightweight_mode_cb.setChecked(getattr(self.config, "LIGHTWEIGHT_MODE", False))
        self.lightweight_mode_cb.toggled.connect(self._on_lightweight_mode_toggled)
        self.lightweight_hint_label = QLabel(
            "※低スペックPC向けにエンコード負荷・音声処理を軽量化します（各設定は個別に変更可能です）"
            if getattr(self.config, "LANGUAGE", "ja") == "ja"
            else "* Optimizes encoder & audio load for low-spec PCs (individual settings remain customizable)."
        )
        self.lightweight_hint_label.setWordWrap(True)
        self.lightweight_hint_label.setStyleSheet("color: #B8C4D0; font-size: 13px; padding-left: 26px;")
        self.lightweight_hint_label.setVisible(self.lightweight_mode_cb.isChecked())
        lw_layout = QVBoxLayout()
        lw_layout.setContentsMargins(0, 0, 0, 0)
        lw_layout.setSpacing(4)
        lw_layout.addWidget(self.lightweight_mode_cb)
        lw_layout.addWidget(self.lightweight_hint_label)
        video_layout.addRow("", lw_layout)

        self.perf_control_cb = QCheckBox(
            getattr(self.t, "auto_perf_control", "自動負荷コントロールを有効にする (ゲームのFPS低下を防ぐ)")
        )
        self.perf_control_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.perf_control_cb.setChecked(getattr(self.config, "AUTO_PERFORMANCE_CONTROL", True))
        self.perf_control_cb.toggled.connect(self._save_settings)
        video_layout.addRow("", self.perf_control_cb)

        video_group.setLayout(video_layout)
        main_layout.addWidget(video_group)

        self.audio_widget = AudioSettingsWidget(self.config, self.t, self)
        main_layout.addWidget(self.audio_widget)
        main_layout.addStretch()

        self.setLayout(main_layout)

    def _on_lightweight_mode_toggled(self, checked: bool) -> None:
        if hasattr(self, "lightweight_hint_label"):
            self.lightweight_hint_label.setVisible(checked)
        if checked:
            if not self.perf_control_cb.isChecked():
                self.perf_control_cb.blockSignals(True)
                self.perf_control_cb.setChecked(True)
                self.perf_control_cb.blockSignals(False)
            if self.fps_input.currentText() in ("120", "144"):
                self.fps_input.blockSignals(True)
                self.fps_input.setCurrentText("60")
                self.fps_input.blockSignals(False)
            if self.res_input.currentText() == "2560x1440":
                self.res_input.blockSignals(True)
                self.res_input.setCurrentText("1920x1080")
                self.res_input.blockSignals(False)
            cur_enc = self.encoder_input.currentText()
            if cur_enc.startswith("hevc_"):
                h264_cand = cur_enc.replace("hevc_", "h264_", 1)
                h264_idx = self.encoder_input.findText(h264_cand)
                if h264_idx >= 0:
                    self.encoder_input.blockSignals(True)
                    self.encoder_input.setCurrentIndex(h264_idx)
                    self.encoder_input.blockSignals(False)
            self.audio_widget.apply_lightweight_defaults()
        self._save_settings()

    def start_monitors(self) -> None:
        self.audio_widget.start_monitors()

    def stop_monitors(self) -> None:
        self.audio_widget.stop_monitors()

    def refresh_from_config(self) -> None:
        cur_fps = str(getattr(self.config, "RECORD_FPS", "60"))
        self.fps_input.blockSignals(True)
        if self.fps_input.findText(cur_fps) < 0:
            self.fps_input.addItem(cur_fps)
        self.fps_input.setCurrentText(cur_fps)
        self.fps_input.blockSignals(False)

        cur_enc = str(getattr(self.config, "RECORD_ENCODER", "h264_nvenc"))
        self.encoder_input.blockSignals(True)
        if self.encoder_input.findText(cur_enc) >= 0:
            self.encoder_input.setCurrentText(cur_enc)
        self.encoder_input.blockSignals(False)

        cur_res = str(getattr(self.config, "RECORD_RESOLUTION", "1920x1080"))
        self.res_input.blockSignals(True)
        if "x" in cur_res.lower() and self.res_input.findText(cur_res) < 0:
            self.res_input.addItem(cur_res)
        if self.res_input.findText(cur_res) >= 0:
            self.res_input.setCurrentText(cur_res)
        self.res_input.blockSignals(False)

        cur_vfmt = str(getattr(self.config, "RECORD_VIDEO_FORMAT", "ddagrab")).strip().lower()
        self.video_format_input.blockSignals(True)
        vfmt_idx = self.video_format_input.findData(cur_vfmt if cur_vfmt in ("ddagrab", "gdigrab") else "ddagrab")
        if vfmt_idx >= 0:
            self.video_format_input.setCurrentIndex(vfmt_idx)
        self.video_format_input.blockSignals(False)

        lw_checked = bool(getattr(self.config, "LIGHTWEIGHT_MODE", False))
        self.lightweight_mode_cb.blockSignals(True)
        self.lightweight_mode_cb.setChecked(lw_checked)
        self.lightweight_mode_cb.blockSignals(False)
        if hasattr(self, "lightweight_hint_label"):
            self.lightweight_hint_label.setVisible(lw_checked)

        self.perf_control_cb.blockSignals(True)
        self.perf_control_cb.setChecked(bool(getattr(self.config, "AUTO_PERFORMANCE_CONTROL", True)))
        self.perf_control_cb.blockSignals(False)

        self.audio_widget.refresh_from_config()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_from_config()
        main_win = self.window()
        is_rec = getattr(main_win, "rec_button", None) and main_win.rec_button.isChecked()
        if not is_rec:
            self.start_monitors()

    def _open_spec_dialog(self) -> None:
        from ui.spec_recommend_dialog import SpecRecommendDialog
        dialog = SpecRecommendDialog(self.config, self.t, self)
        dialog.exec()
        self.refresh_from_config()

    def _save_settings(self) -> None:
        self.config.RECORD_FPS = self.fps_input.currentText()
        self.config.RECORD_ENCODER = self.encoder_input.currentText()
        self.config.RECORD_RESOLUTION = self.res_input.currentText()
        self.config.RECORD_VIDEO_FORMAT = self.video_format_input.currentData() or "ddagrab"
        self.config.LIGHTWEIGHT_MODE = self.lightweight_mode_cb.isChecked()
        self.config.AUTO_PERFORMANCE_CONTROL = self.perf_control_cb.isChecked()
        self.config.save()
