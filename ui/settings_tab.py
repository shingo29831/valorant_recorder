from PyQt6.QtWidgets import (QWidget, QFormLayout, QLineEdit, QComboBox, 
                             QPushButton, QMessageBox, QVBoxLayout, QLabel, 
                             QHBoxLayout, QSlider, QCheckBox)
from PyQt6.QtCore import pyqtSignal, Qt, QThread
from PyQt6.QtGui import QPainter, QColor
from core.config import Config
import numpy as np

class VolumeMeter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(20)
        self.level = 0.0
        self.gate_threshold = 0.0

    def set_level(self, level):
        self.level = min(1.0, max(0.0, level))
        self.update()

    def set_gate_threshold(self, threshold):
        self.gate_threshold = min(1.0, max(0.0, threshold))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        width = self.width()
        height = self.height()

        w_green = int(width * 0.6)
        w_yellow = int(width * 0.85)

        # 背景として各ゾーンを薄い色で描画
        painter.fillRect(0, 0, w_green, height, QColor(0, 255, 0, 40))
        painter.fillRect(w_green, 0, w_yellow - w_green, height, QColor(255, 255, 0, 40))
        painter.fillRect(w_yellow, 0, width - w_yellow, height, QColor(255, 0, 0, 40))

        # 現在のレベルに応じて濃い色を上書き
        if self.level > 0:
            draw_width = int(width * self.level)
            
            # ゲート閾値以下の場合はグレーで描画してカットされていることを示す
            if self.level <= self.gate_threshold and self.gate_threshold > 0:
                painter.fillRect(0, 0, draw_width, height, QColor(150, 150, 150))
            else:
                if draw_width > 0:
                    painter.fillRect(0, 0, min(draw_width, w_green), height, QColor(0, 255, 0))
                if draw_width > w_green:
                    painter.fillRect(w_green, 0, min(draw_width, w_yellow) - w_green, height, QColor(255, 255, 0))
                if draw_width > w_yellow:
                    painter.fillRect(w_yellow, 0, draw_width - w_yellow, height, QColor(255, 0, 0))

        # ゲートの閾値ラインを描画
        if self.gate_threshold > 0:
            gate_x = int(width * self.gate_threshold)
            painter.setPen(QColor(255, 255, 255))
            painter.drawLine(gate_x, 0, gate_x, height)

class MicMonitorThread(QThread):
    level_ready = pyqtSignal(float)

    def __init__(self, mic_name, gain, denoise=False):
        super().__init__()
        self.mic_name = mic_name
        self.gain = gain
        self.denoise = denoise
        self.running = True
        self.noise_floor = 0.01

    def set_gain(self, gain):
        self.gain = gain

    def set_denoise(self, denoise):
        self.denoise = denoise

    def audio_callback(self, indata, frames, time, status):
        if not self.running:
            return
        data = indata * self.gain

        if self.denoise:
            # ブロック全体のエネルギー(RMS)を計算
            rms = np.sqrt(np.mean(data**2) + 1e-8)
            
            # ノイズフロアの動的推定
            if rms < self.noise_floor:
                self.noise_floor = 0.8 * self.noise_floor + 0.2 * rms
            else:
                self.noise_floor = 0.995 * self.noise_floor + 0.005 * rms
            
            # Signal-to-Noise Ratio (SNR) の計算
            snr = rms / self.noise_floor
            
            # SNRが低い（定常ノイズのみ）場合は、信号全体を強く減衰させる
            # FFmpegの afftdn=-25dB (約0.05倍) に近い挙動をシミュレート
            if snr < 3.0:
                reduction = max(0.05, (snr - 1.0) / 2.0)
                data = data * reduction

        peak = np.max(np.abs(data))
        level = min(1.0, peak ** 0.5)
        self.level_ready.emit(float(level))

    def run(self):
        try:
            import soundcard as sc
            
            mic_device = None
            if self.mic_name and self.mic_name != "None":
                for m in sc.all_microphones(include_loopback=False):
                    if self.mic_name in m.name:
                        mic_device = m
                        break
                if mic_device is None:
                    mic_device = sc.default_microphone()

            if mic_device is not None:
                with mic_device.recorder(samplerate=48000, channels=1) as recorder:
                    while self.running:
                        data = recorder.record(numframes=2400)
                        self.audio_callback(data, 2400, None, None)
            else:
                while self.running:
                    self.level_ready.emit(0.0)
                    self.msleep(50)
        except Exception as e:
            import traceback
            import os
            log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mic_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"MicMonitorThread error:\n{traceback.format_exc()}\n")

    def stop(self):
        self.running = False
        self.wait()

class SettingsTab(QWidget):
    backRequested = pyqtSignal()

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(40, 40, 40, 40)
        
        header_layout = QHBoxLayout()
        title = QLabel("APPLICATION SETTINGS")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #FF4655;")
        
        back_btn = QPushButton("← BACK TO RECORDINGS")
        back_btn.setFixedWidth(200)
        back_btn.clicked.connect(self.backRequested.emit)
        
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(back_btn)
        
        main_layout.addLayout(header_layout)
        main_layout.addSpacing(20)
        
        form_layout = QFormLayout()
        form_layout.setSpacing(15)
        
        self.fps_input = QComboBox()
        self.fps_input.addItems(["30", "60", "120", "144"])
        self.fps_input.setCurrentText(self.config.RECORD_FPS)
        
        self.encoder_input = QComboBox()
        self.encoder_input.addItems(["h264_nvenc", "libx264", "hevc_nvenc"])
        self.encoder_input.setCurrentText(self.config.RECORD_ENCODER)
        
        self.res_input = QComboBox()
        self.res_input.addItems(["1920x1080", "2560x1440", "1280x720"])
        self.res_input.setCurrentText(self.config.RECORD_RESOLUTION)
        
        self.riot_id_input = QLineEdit(self.config.RIOT_ID)
        self.tag_line_input = QLineEdit(self.config.TAG_LINE)
        self.api_key_input = QLineEdit(self.config.API_KEY)
        
        form_layout.addRow("Riot ID:", self.riot_id_input)
        form_layout.addRow("Tag Line:", self.tag_line_input)
        form_layout.addRow("Henrik API Key:", self.api_key_input)
        form_layout.addRow("Recording FPS:", self.fps_input)
        form_layout.addRow("Encoder:", self.encoder_input)
        form_layout.addRow("Resolution:", self.res_input)
        
        self.mic_input = QComboBox()
        self.mic_input.addItem("None")
        try:
            import soundcard as sc
            seen = set()
            for m in sc.all_microphones(include_loopback=False):
                name = m.name
                if name not in seen:
                    self.mic_input.addItem(name)
                    seen.add(name)
        except Exception as e:
            import traceback
            print("=== マイクデバイス取得エラー ===")
            traceback.print_exc()
            print("================================")
        
        idx = self.mic_input.findText(self.config.RECORD_AUDIO_MIC)
        if idx >= 0:
            self.mic_input.setCurrentIndex(idx)

        self.mic_gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_gain_slider.setRange(0, 300)
        gain_val = float(getattr(self.config, 'RECORD_AUDIO_MIC_GAIN', '1.0'))
        self.mic_gain_slider.setValue(int(gain_val * 100))
        
        self.mic_gain_label = QLabel(f"{gain_val:.2f}x")
        self.mic_gain_label.setFixedWidth(40)
        
        gain_layout = QHBoxLayout()
        gain_layout.addWidget(self.mic_gain_slider)
        gain_layout.addWidget(self.mic_gain_label)
        
        self.mic_denoise_cb = QCheckBox("Enable Noise Cancellation (AI/FFT based)")
        self.mic_denoise_cb.setChecked(getattr(self.config, 'RECORD_AUDIO_MIC_DENOISE', 'False') == 'True')

        self.mic_gate_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_gate_slider.setRange(0, 100)
        gate_val = int(getattr(self.config, 'RECORD_AUDIO_MIC_NOISE_GATE', '0'))
        self.mic_gate_slider.setValue(gate_val)
        
        self.mic_gate_label = QLabel(f"{gate_val}%")
        self.mic_gate_label.setFixedWidth(40)
        
        gate_layout = QHBoxLayout()
        gate_layout.addWidget(self.mic_gate_slider)
        gate_layout.addWidget(self.mic_gate_label)

        self.volume_meter = VolumeMeter()
        self.volume_meter.set_gate_threshold(gate_val / 100.0)
        
        form_layout.addRow("Microphone:", self.mic_input)
        form_layout.addRow("Mic Gain:", gain_layout)
        form_layout.addRow("Noise Cancel:", self.mic_denoise_cb)
        form_layout.addRow("Noise Gate:", gate_layout)
        form_layout.addRow("Mic Level:", self.volume_meter)
        
        main_layout.addLayout(form_layout)
        
        self.mic_gain_slider.valueChanged.connect(self._on_gain_changed)
        self.mic_gate_slider.valueChanged.connect(self._on_gate_changed)
        self.mic_denoise_cb.stateChanged.connect(self._on_denoise_changed)
        self.mic_input.currentIndexChanged.connect(self._on_mic_changed)
        self.monitor_thread = None
        
        save_btn = QPushButton("SAVE SETTINGS")
        save_btn.setFixedWidth(200)
        save_btn.setStyleSheet("margin-top: 30px;")
        save_btn.clicked.connect(self.save_settings)
        main_layout.addWidget(save_btn)
        
        main_layout.addStretch()
        self.setLayout(main_layout)

    def _on_gain_changed(self, value):
        gain = value / 100.0
        self.mic_gain_label.setText(f"{gain:.2f}x")
        if self.monitor_thread:
            self.monitor_thread.set_gain(gain)

    def _on_gate_changed(self, value):
        self.mic_gate_label.setText(f"{value}%")
        self.volume_meter.set_gate_threshold(value / 100.0)

    def _on_denoise_changed(self, state):
        if self.monitor_thread:
            self.monitor_thread.set_denoise(self.mic_denoise_cb.isChecked())

    def _on_mic_changed(self):
        self._stop_mic_monitor()
        if self.isVisible():
            self._start_mic_monitor()

    def _start_mic_monitor(self):
        if self.monitor_thread is not None:
            return
        mic_name = self.mic_input.currentText()
        gain = self.mic_gain_slider.value() / 100.0
        denoise = self.mic_denoise_cb.isChecked()
        self.monitor_thread = MicMonitorThread(mic_name, gain, denoise)
        self.monitor_thread.level_ready.connect(self.volume_meter.set_level)
        self.monitor_thread.start()

    def _stop_mic_monitor(self):
        if self.monitor_thread:
            self.monitor_thread.stop()
            self.monitor_thread = None
            self.volume_meter.set_level(0.0)

    def showEvent(self, event):
        super().showEvent(event)
        self._start_mic_monitor()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._stop_mic_monitor()

    def save_settings(self):
        self.config.RIOT_ID = self.riot_id_input.text()
        self.config.TAG_LINE = self.tag_line_input.text()
        self.config.API_KEY = self.api_key_input.text()
        self.config.RECORD_FPS = self.fps_input.currentText()
        self.config.RECORD_ENCODER = self.encoder_input.currentText()
        self.config.RECORD_RESOLUTION = self.res_input.currentText()
        
        mic_val = self.mic_input.currentText()
        self.config.RECORD_AUDIO_MIC = "" if mic_val == "None" else mic_val
        self.config.RECORD_AUDIO_MIC_GAIN = str(self.mic_gain_slider.value() / 100.0)
        self.config.RECORD_AUDIO_MIC_NOISE_GATE = str(self.mic_gate_slider.value())
        self.config.RECORD_AUDIO_MIC_DENOISE = str(self.mic_denoise_cb.isChecked())
        
        self.config.save()
        QMessageBox.information(self, "Success", "Settings saved successfully.\nPlease restart the application to apply changes.")