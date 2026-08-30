import os
from PyQt6.QtWidgets import (QWidget, QFormLayout, QComboBox, QVBoxLayout, QLabel, 
                             QGroupBox, QSlider, QHBoxLayout, QCheckBox, QMessageBox)
from PyQt6.QtCore import Qt
from ui.volume_meter import VolumeMeter
from ui.audio_monitor_threads import SystemAudioMonitorThread, MicMonitorThread

class RecordSettingsWidget(QWidget):
    def __init__(self, config, t, parent=None):
        super().__init__(parent)
        self.config = config
        self.t = t
        
        self.monitor_thread = None
        self.sys_monitor_thread = None
        
        main_layout = QVBoxLayout()
        main_layout.setSpacing(15)
        
        # Video Settings Group
        video_group = QGroupBox(getattr(self.t, 'video_settings', "Video Settings"))
        video_layout = QFormLayout()
        video_layout.setSpacing(15)
        
        self.fps_input = QComboBox()
        self.fps_input.addItems(["30", "60", "120", "144"])
        self.fps_input.setCurrentText(self.config.RECORD_FPS)
        self.fps_input.currentTextChanged.connect(self._save_settings)
        
        from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded
        from recorder.ffmpeg_recorder import get_available_encoders
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ffmpeg_path = ensure_ffmpeg_downloaded(project_root)
        available_encoders, encoder_warning_keys = get_available_encoders(ffmpeg_path)
        
        self.encoder_input = QComboBox()
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
        encoder_layout.addWidget(self.encoder_input)
        if encoder_warning_keys:
            warning_texts = [getattr(self.t, key, key) for key in encoder_warning_keys]
            warning_label = QLabel("\n".join(warning_texts))
            warning_label.setStyleSheet("color: #FFaa00; font-size: 11px; font-weight: bold;")
            encoder_layout.addWidget(warning_label)
        encoder_layout.setSpacing(2)
        
        self.res_input = QComboBox()
        self.res_input.addItems(["1920x1080", "2560x1440", "1280x720"])
        self.res_input.setCurrentText(self.config.RECORD_RESOLUTION)
        self.res_input.currentTextChanged.connect(self._save_settings)
        
        video_layout.addRow(self.t.recording_fps, self.fps_input)
        video_layout.addRow(self.t.encoder, encoder_layout)
        video_layout.addRow(self.t.resolution, self.res_input)
        
        video_group.setLayout(video_layout)
        main_layout.addWidget(video_group)
        
        # Audio Settings Group
        audio_group = QGroupBox(getattr(self.t, 'audio_settings', "Audio Settings"))
        audio_layout = QFormLayout()
        audio_layout.setSpacing(15)
        
        self.system_gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.system_gain_slider.setRange(0, 300)
        sys_gain_val = float(getattr(self.config, 'RECORD_AUDIO_SYSTEM_GAIN', '1.0'))
        self.system_gain_slider.setValue(int(sys_gain_val * 100))
        self.system_gain_slider.valueChanged.connect(self._on_system_gain_changed)
        
        self.system_gain_label = QLabel(f"{sys_gain_val:.2f}x")
        self.system_gain_label.setFixedWidth(40)
        
        sys_gain_layout = QHBoxLayout()
        sys_gain_layout.addWidget(self.system_gain_slider)
        sys_gain_layout.addWidget(self.system_gain_label)
        
        self.system_volume_meter = VolumeMeter()
        
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
        except Exception:
            pass
        
        idx = self.mic_input.findText(self.config.RECORD_AUDIO_MIC)
        if idx >= 0:
            self.mic_input.setCurrentIndex(idx)
        self.mic_input.currentIndexChanged.connect(self._on_mic_changed)

        self.mic_gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_gain_slider.setRange(0, 300)
        gain_val = float(getattr(self.config, 'RECORD_AUDIO_MIC_GAIN', '1.0'))
        self.mic_gain_slider.setValue(int(gain_val * 100))
        self.mic_gain_slider.valueChanged.connect(self._on_gain_changed)
        
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
        self.mic_denoise_combo.currentIndexChanged.connect(self._on_denoise_changed)

        self.mic_gate_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_gate_slider.setRange(0, 100)
        gate_val = int(getattr(self.config, 'RECORD_AUDIO_MIC_NOISE_GATE', '0'))
        self.mic_gate_slider.setValue(gate_val)
        self.mic_gate_slider.valueChanged.connect(self._on_gate_changed)
        
        self.mic_gate_label = QLabel(f"{gate_val}%")
        self.mic_gate_label.setFixedWidth(40)
        
        gate_layout = QHBoxLayout()
        gate_layout.addWidget(self.mic_gate_slider)
        gate_layout.addWidget(self.mic_gate_label)

        self.volume_meter = VolumeMeter()
        self.volume_meter.set_gate_threshold(gate_val / 100.0)
        
        self.mic_monitor_cb = QCheckBox(self.t.listen_to_mic)
        self.mic_monitor_cb.setChecked(False)
        self.mic_monitor_cb.stateChanged.connect(self._on_monitor_changed)
        
        self.monitor_warning_label = QLabel(self.t.monitor_warning)
        self.monitor_warning_label.setStyleSheet("color: #AAAAAA; font-size: 11px; font-style: italic;")
        self.monitor_warning_label.setVisible(denoise_val == "AI (RNNoise)")
        
        monitor_layout = QVBoxLayout()
        monitor_layout.addWidget(self.mic_monitor_cb)
        monitor_layout.addWidget(self.monitor_warning_label)
        monitor_layout.setSpacing(2)
        
        audio_layout.addRow(self.t.system_gain, sys_gain_layout)
        audio_layout.addRow(self.t.system_level, self.system_volume_meter)
        audio_layout.addRow(self.t.microphone, self.mic_input)
        audio_layout.addRow(self.t.mic_gain, gain_layout)
        audio_layout.addRow(self.t.noise_cancel, self.mic_denoise_combo)
        audio_layout.addRow(self.t.noise_gate, gate_layout)
        audio_layout.addRow(self.t.mic_level, self.volume_meter)
        audio_layout.addRow("", monitor_layout)
        
        audio_group.setLayout(audio_layout)
        main_layout.addWidget(audio_group)
        
        self.setLayout(main_layout)

    def _on_system_gain_changed(self, value):
        gain = value / 100.0
        self.system_gain_label.setText(f"{gain:.2f}x")
        if self.sys_monitor_thread:
            self.sys_monitor_thread.set_gain(gain)
        self._save_settings()

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
        self.stop_monitors()
        if self.isVisible():
            self.start_monitors()
        self._save_settings()

    def start_monitors(self):
        if self.monitor_thread is None:
            mic_name = self.mic_input.currentText()
            gain = self.mic_gain_slider.value() / 100.0
            denoise = self.mic_denoise_combo.currentText() == "AI (RNNoise)"
            gate = self.mic_gate_slider.value() / 100.0
            self.monitor_thread = MicMonitorThread(mic_name, gain, denoise, gate)
            self.monitor_thread.set_monitor_audio(self.mic_monitor_cb.isChecked())
            self.monitor_thread.level_ready.connect(self.volume_meter.set_level)
            self.monitor_thread.start()
            
        if self.sys_monitor_thread is None:
            gain = self.system_gain_slider.value() / 100.0
            self.sys_monitor_thread = SystemAudioMonitorThread(gain)
            self.sys_monitor_thread.level_ready.connect(self.system_volume_meter.set_level)
            self.sys_monitor_thread.start()

    def stop_monitors(self):
        if self.monitor_thread:
            self.monitor_thread.stop()
            self.monitor_thread = None
            self.volume_meter.set_level(0.0)
            
        if self.sys_monitor_thread:
            self.sys_monitor_thread.stop()
            self.sys_monitor_thread = None
            self.system_volume_meter.set_level(0.0)

    def _save_settings(self):
        self.config.RECORD_FPS = self.fps_input.currentText()
        self.config.RECORD_ENCODER = self.encoder_input.currentText()
        self.config.RECORD_RESOLUTION = self.res_input.currentText()
        
        self.config.RECORD_AUDIO_SYSTEM_GAIN = str(self.system_gain_slider.value() / 100.0)
        mic_val = self.mic_input.currentText()
        self.config.RECORD_AUDIO_MIC = "" if mic_val == "None" else mic_val
        self.config.RECORD_AUDIO_MIC_GAIN = str(self.mic_gain_slider.value() / 100.0)
        self.config.RECORD_AUDIO_MIC_NOISE_GATE = str(self.mic_gate_slider.value())
        self.config.RECORD_AUDIO_MIC_DENOISE = self.mic_denoise_combo.currentText()
        
        self.config.save()
