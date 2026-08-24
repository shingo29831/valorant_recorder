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

    def __init__(self, mic_name, gain, denoise=False, gate_threshold=0.0):
        super().__init__()
        self.mic_name = mic_name
        self.gain = gain
        self.denoise = denoise
        self.gate_threshold = gate_threshold
        self.monitor_audio = False
        self.running = True
        self.noise_floor = 0.01
        self.gate_open = False

    def set_gain(self, gain):
        self.gain = gain

    def set_denoise(self, denoise):
        self.denoise = denoise

    def set_gate_threshold(self, threshold):
        self.gate_threshold = threshold

    def set_monitor_audio(self, monitor):
        self.monitor_audio = monitor

    def process_audio(self, data):
        data = data * self.gain

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
            # FFmpegの arnndn に近い挙動をシミュレート
            if snr < 3.0:
                reduction = max(0.05, (snr - 1.0) / 2.0)
                data = data * reduction

        # メーター表示用のレベル計算（ゲート適用前）
        peak = np.max(np.abs(data))
        level = min(1.0, peak ** 0.5)
        self.level_ready.emit(float(level))

        # 再生用のノイズゲート適用
        if self.gate_threshold > 0:
            amp_threshold = (self.gate_threshold / 2.0) ** 2
            rms = np.sqrt(np.mean(data**2) + 1e-8)
            if rms > amp_threshold:
                self.gate_open = True
            elif rms < amp_threshold * 0.5: # ヒステリシス
                self.gate_open = False
            
            if not self.gate_open:
                data = data * 0.01

        return data

    def run(self):
        try:
            import soundcard as sc
            
            mic_device = None
            if self.mic_name and self.mic_name != "None":
                # 完全一致を優先
                for m in sc.all_microphones(include_loopback=False):
                    if self.mic_name == m.name:
                        mic_device = m
                        break
                # 見つからなければ部分一致
                if mic_device is None:
                    for m in sc.all_microphones(include_loopback=False):
                        if self.mic_name in m.name:
                            mic_device = m
                            break
                # フォールバックを廃止し、見つからない場合は None のままにする

            if mic_device is not None:
                try:
                    recorder = mic_device.recorder(samplerate=48000, channels=2)
                    channels = 2
                except Exception:
                    try:
                        recorder = mic_device.recorder(samplerate=48000, channels=1)
                        channels = 1
                    except Exception as e:
                        import traceback
                        print(f"Failed to initialize recorder:")
                        traceback.print_exc()
                        recorder = None
                
                try:
                    speaker = sc.default_speaker()
                    player = speaker.player(samplerate=48000, channels=2)
                except Exception:
                    player = None
                    
                if recorder is not None:
                    with recorder:
                        if player is not None:
                            with player:
                                while self.running:
                                    data = recorder.record(numframes=2400)
                                    if channels == 2:
                                        # ステレオの場合は平均をとってモノラルにダウンミックス
                                        data = data.mean(axis=1, keepdims=True)
                                    processed = self.process_audio(data)
                                    if self.monitor_audio:
                                        # モノラルをステレオに複製して再生
                                        stereo = np.repeat(processed, 2, axis=1)
                                        player.play(stereo)
                        else:
                            while self.running:
                                data = recorder.record(numframes=2400)
                                if channels == 2:
                                    data = data.mean(axis=1, keepdims=True)
                                self.process_audio(data)
                else:
                    while self.running:
                        self.level_ready.emit(0.0)
                        self.msleep(50)
            else:
                while self.running:
                    self.level_ready.emit(0.0)
                    self.msleep(50)
        except Exception as e:
            import traceback
            import os
            print("\n=== MicMonitorThread Error ===")
            traceback.print_exc()
            print("==============================\n")
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
        
        self.mic_denoise_combo = QComboBox()
        self.mic_denoise_combo.addItems(["None", "AI (RNNoise)", "NVIDIA Broadcast"])
        
        denoise_val = getattr(self.config, 'RECORD_AUDIO_MIC_DENOISE', 'None')
        if denoise_val in ('True', 'Standard (FFmpeg)'):
            denoise_val = 'AI (RNNoise)'
        elif denoise_val == 'False':
            denoise_val = 'None'
            
        idx = self.mic_denoise_combo.findText(denoise_val)
        if idx >= 0:
            self.mic_denoise_combo.setCurrentIndex(idx)

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
        
        self.mic_monitor_cb = QCheckBox("Listen to Microphone (Monitor)")
        self.mic_monitor_cb.setChecked(False)
        
        self.monitor_warning_label = QLabel("Note: AI (RNNoise) effect is applied only in actual recordings, not in this monitor.")
        self.monitor_warning_label.setStyleSheet("color: #AAAAAA; font-size: 11px; font-style: italic;")
        self.monitor_warning_label.setVisible(False)
        
        monitor_layout = QVBoxLayout()
        monitor_layout.addWidget(self.mic_monitor_cb)
        monitor_layout.addWidget(self.monitor_warning_label)
        monitor_layout.setSpacing(2)
        
        form_layout.addRow("Microphone:", self.mic_input)
        form_layout.addRow("Mic Gain:", gain_layout)
        form_layout.addRow("Noise Cancel:", self.mic_denoise_combo)
        form_layout.addRow("Noise Gate:", gate_layout)
        form_layout.addRow("Mic Level:", self.volume_meter)
        form_layout.addRow("", monitor_layout)
        
        main_layout.addLayout(form_layout)
        
        self.mic_gain_slider.valueChanged.connect(self._on_gain_changed)
        self.mic_gate_slider.valueChanged.connect(self._on_gate_changed)
        self.mic_denoise_combo.currentIndexChanged.connect(self._on_denoise_changed)
        self.mic_input.currentIndexChanged.connect(self._on_mic_changed)
        self.mic_monitor_cb.stateChanged.connect(self._on_monitor_changed)
        self.monitor_thread = None
        
        self.riot_id_input.textChanged.connect(self._save_settings)
        self.tag_line_input.textChanged.connect(self._save_settings)
        self.api_key_input.textChanged.connect(self._save_settings)
        self.fps_input.currentTextChanged.connect(self._save_settings)
        self.encoder_input.currentTextChanged.connect(self._save_settings)
        self.res_input.currentTextChanged.connect(self._save_settings)
        
        main_layout.addStretch()
        self.setLayout(main_layout)

    def _on_gain_changed(self, value):
        gain = value / 100.0
        self.mic_gain_label.setText(f"{gain:.2f}x")
        if self.monitor_thread:
            self.monitor_thread.set_gain(gain)
        self._save_settings()

    def _on_gate_changed(self, value):
        self.mic_gate_label.setText(f"{value}%")
        self.volume_meter.set_gate_threshold(value / 100.0)
        if self.monitor_thread:
            self.monitor_thread.set_gate_threshold(value / 100.0)
        self._save_settings()

    def _on_monitor_changed(self, state):
        if self.monitor_thread:
            self.monitor_thread.set_monitor_audio(self.mic_monitor_cb.isChecked())

    def _on_denoise_changed(self, index):
        mode = self.mic_denoise_combo.currentText()
        
        if hasattr(self, 'monitor_warning_label'):
            self.monitor_warning_label.setVisible(mode == "AI (RNNoise)")
            
        if mode == "NVIDIA Broadcast":
            found = False
            for i in range(self.mic_input.count()):
                if "NVIDIA Broadcast" in self.mic_input.itemText(i):
                    self.mic_input.setCurrentIndex(i)
                    found = True
                    break
            if not found:
                QMessageBox.warning(self, "NVIDIA Broadcast Not Found", 
                                    "NVIDIA Broadcast microphone was not found in the device list.\n\n"
                                    "Please ensure the NVIDIA Broadcast app is installed, running, and the microphone effect is turned on.")
                # 見つからなかった場合はAI (RNNoise)に戻す
                idx = self.mic_denoise_combo.findText("AI (RNNoise)")
                if idx >= 0:
                    self.mic_denoise_combo.blockSignals(True)
                    self.mic_denoise_combo.setCurrentIndex(idx)
                    self.mic_denoise_combo.blockSignals(False)
                mode = "AI (RNNoise)"
        
        if self.monitor_thread:
            self.monitor_thread.set_denoise(mode == "AI (RNNoise)")
        self._save_settings()

    def _on_mic_changed(self):
        self._stop_mic_monitor()
        if self.isVisible():
            self._start_mic_monitor()
        self._save_settings()

    def _start_mic_monitor(self):
        if self.monitor_thread is not None:
            return
        mic_name = self.mic_input.currentText()
        gain = self.mic_gain_slider.value() / 100.0
        denoise = self.mic_denoise_combo.currentText() == "AI (RNNoise)"
        gate = self.mic_gate_slider.value() / 100.0
        self.monitor_thread = MicMonitorThread(mic_name, gain, denoise, gate)
        self.monitor_thread.set_monitor_audio(self.mic_monitor_cb.isChecked())
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

    def _save_settings(self, *args):
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
        self.config.RECORD_AUDIO_MIC_DENOISE = self.mic_denoise_combo.currentText()
        
        self.config.save()