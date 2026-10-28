import os
from PyQt6.QtWidgets import (QWidget, QFormLayout, QComboBox, QVBoxLayout, QLabel, 
                             QGroupBox, QSlider, QHBoxLayout, QCheckBox, QMessageBox)
from PyQt6.QtCore import Qt, QTimer
from ui.volume_meter import VolumeMeter
from ui.audio_monitor_threads import SystemAudioMonitorThread, MicMonitorThread

class RecordSettingsWidget(QWidget):
    def __init__(self, config, t, parent=None):
        super().__init__(parent)
        self.config = config
        self.t = t
        
        self.monitor_thread = None
        self.sys_monitor_thread = None
        self._stopping_threads = []
        
        main_layout = QVBoxLayout()
        main_layout.setSpacing(15)
        
        # Video Settings Group
        video_group = QGroupBox(self.t.video_settings)
        video_layout = QFormLayout()
        video_layout.setSpacing(15)
        
        self.fps_input = QComboBox()
        fps_items = ["30", "60", "120", "144"]
        cur_fps = str(getattr(self.config, 'RECORD_FPS', '60'))
        if cur_fps and cur_fps not in fps_items:
            fps_items.append(cur_fps)
        self.fps_input.addItems(fps_items)
        self.fps_input.setCurrentText(cur_fps if cur_fps in fps_items else "60")
        self.fps_input.currentTextChanged.connect(self._save_settings)
        
        from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded
        from recorder.ffmpeg_recorder import get_available_encoders
        app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValoReco')
        os.makedirs(app_data_dir, exist_ok=True)
        ffmpeg_path = ensure_ffmpeg_downloaded(app_data_dir)
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
        res_items = ["2560x1440", "1920x1080", "1600x900", "1280x720"]
        cur_res = str(getattr(self.config, 'RECORD_RESOLUTION', '1920x1080'))
        if cur_res and "x" in cur_res.lower() and cur_res not in res_items:
            res_items.append(cur_res)
        self.res_input.addItems(res_items)
        if cur_res in res_items:
            self.res_input.setCurrentText(cur_res)
        else:
            self.res_input.setCurrentText("1920x1080")
        self.res_input.currentTextChanged.connect(self._save_settings)

        self.video_format_input = QComboBox()
        self.video_format_input.addItem("ddagrab (GPU Direct)", "ddagrab")
        self.video_format_input.addItem("gdigrab (Compatibility)", "gdigrab")
        cur_vfmt = str(getattr(self.config, 'RECORD_VIDEO_FORMAT', 'ddagrab')).strip().lower()
        vfmt_idx = self.video_format_input.findData(cur_vfmt if cur_vfmt in ("ddagrab", "gdigrab") else "ddagrab")
        if vfmt_idx >= 0:
            self.video_format_input.setCurrentIndex(vfmt_idx)
        self.video_format_input.currentIndexChanged.connect(self._save_settings)
        
        video_layout.addRow(self.t.recording_fps, self.fps_input)
        video_layout.addRow(self.t.encoder, encoder_layout)
        video_layout.addRow(self.t.resolution, self.res_input)
        video_layout.addRow(getattr(self.t, 'video_format', "Capture Mode:"), self.video_format_input)

        self.lightweight_mode_cb = QCheckBox(getattr(self.t, 'lightweight_mode', "軽量モードを有効にする (低負荷録画・省メモリ動作)"))
        self.lightweight_mode_cb.setChecked(getattr(self.config, 'LIGHTWEIGHT_MODE', False))
        self.lightweight_mode_cb.toggled.connect(self._on_lightweight_mode_toggled)
        self.lightweight_hint_label = QLabel(
            "※低スペックPC向けにエンコード負荷・音声処理を軽量化します（各設定は個別に変更可能です）"
            if getattr(self.config, 'LANGUAGE', 'ja') == 'ja'
            else "* Optimizes encoder & audio load for low-spec PCs (individual settings remain customizable)."
        )
        self.lightweight_hint_label.setStyleSheet("color: #AAAAAA; font-size: 11px;")
        self.lightweight_hint_label.setVisible(self.lightweight_mode_cb.isChecked())
        lw_layout = QVBoxLayout()
        lw_layout.setSpacing(2)
        lw_layout.addWidget(self.lightweight_mode_cb)
        lw_layout.addWidget(self.lightweight_hint_label)
        video_layout.addRow("", lw_layout)

        self.perf_control_cb = QCheckBox(getattr(self.t, 'auto_perf_control', "自動負荷コントロールを有効にする (ゲームのFPS低下を防ぐ)"))
        self.perf_control_cb.setChecked(getattr(self.config, 'AUTO_PERFORMANCE_CONTROL', True))
        self.perf_control_cb.toggled.connect(self._save_settings)
        video_layout.addRow("", self.perf_control_cb)
        
        video_group.setLayout(video_layout)
        main_layout.addWidget(video_group)
        
        # Audio Settings Group
        audio_group = QGroupBox(self.t.audio_settings)
        audio_layout = QFormLayout()
        audio_layout.setSpacing(15)
        
        self.sys_audio_input = QComboBox()
        default_label = getattr(self.t, 'default_device', "Default")
        self.sys_audio_input.addItem(default_label, "default")
        self.sys_audio_input.addItem(self.t.none_option, "None")
        try:
            import soundcard as sc
            seen_spk = set()
            for spk in sc.all_speakers():
                name = spk.name
                if name not in seen_spk:
                    self.sys_audio_input.addItem(name, name)
                    seen_spk.add(name)
        except Exception:
            pass
        cur_sys_dev = str(getattr(self.config, 'RECORD_AUDIO_SYSTEM', 'default')).strip()
        if not cur_sys_dev or cur_sys_dev.lower() == "default":
            cur_sys_dev = "default"
        elif cur_sys_dev.lower() == "none":
            cur_sys_dev = "None"
        elif self.sys_audio_input.findData(cur_sys_dev) < 0:
            self.sys_audio_input.addItem(cur_sys_dev, cur_sys_dev)
        sys_idx = self.sys_audio_input.findData(cur_sys_dev)
        if sys_idx >= 0:
            self.sys_audio_input.setCurrentIndex(sys_idx)
        self.sys_audio_input.currentIndexChanged.connect(self._on_sys_audio_changed)

        self.system_gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.system_gain_slider.setRange(0, 300)
        sys_gain_val = float(getattr(self.config, 'RECORD_AUDIO_SYSTEM_GAIN', '1.0'))
        if sys_gain_val > 10.0:
            sys_gain_val = sys_gain_val / 100.0
        self.system_gain_slider.setValue(int(round(sys_gain_val * 100)))
        self.system_gain_slider.valueChanged.connect(self._on_system_gain_changed)
        
        self.system_gain_label = QLabel(f"{sys_gain_val:.2f}x")
        self.system_gain_label.setFixedWidth(40)
        
        sys_gain_layout = QHBoxLayout()
        sys_gain_layout.addWidget(self.system_gain_slider)
        sys_gain_layout.addWidget(self.system_gain_label)
        
        self.system_volume_meter = VolumeMeter()
        
        self.mic_input = QComboBox()
        self.mic_input.addItem(self.t.none_option, "None")
        self.mic_input.addItem(default_label, "default")
        try:
            import soundcard as sc
            seen = set()
            for m in sc.all_microphones(include_loopback=False):
                name = m.name
                if name not in seen:
                    self.mic_input.addItem(name, name)
                    seen.add(name)
        except Exception:
            pass
        
        cur_mic = str(getattr(self.config, 'RECORD_AUDIO_MIC', '')).strip()
        if cur_mic.lower() == "none":
            target_mic_data = "None"
        elif not cur_mic or cur_mic.lower() == "default":
            target_mic_data = "default"
        else:
            if self.mic_input.findData(cur_mic) < 0:
                self.mic_input.addItem(cur_mic, cur_mic)
            target_mic_data = cur_mic
        idx = self.mic_input.findData(target_mic_data)
        if idx >= 0:
            self.mic_input.setCurrentIndex(idx)
        self.mic_input.currentIndexChanged.connect(self._on_mic_changed)

        self.mic_gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_gain_slider.setRange(0, 300)
        gain_val = float(getattr(self.config, 'RECORD_AUDIO_MIC_GAIN', '1.0'))
        if gain_val > 10.0:
            gain_val = gain_val / 100.0
        self.mic_gain_slider.setValue(int(round(gain_val * 100)))
        self.mic_gain_slider.valueChanged.connect(self._on_gain_changed)
        
        self.mic_gain_label = QLabel(f"{gain_val:.2f}x")
        self.mic_gain_label.setFixedWidth(40)
        
        gain_layout = QHBoxLayout()
        gain_layout.addWidget(self.mic_gain_slider)
        gain_layout.addWidget(self.mic_gain_label)
        
        self.mic_denoise_combo = QComboBox()
        self.mic_denoise_combo.addItem(self.t.none_option, "None")
        
        from recorder.audio_processor_wrapper import AudioProcessorWrapper
        if AudioProcessorWrapper.is_available():
            self.mic_denoise_combo.addItem("AI (DeepFilterNet)", "AI (DeepFilterNet)")
            
        self.mic_denoise_combo.addItem("AI (RNNoise)", "AI (RNNoise)")
        self.mic_denoise_combo.addItem("NVIDIA Broadcast", "NVIDIA Broadcast")
        
        denoise_val = getattr(self.config, 'RECORD_AUDIO_MIC_DENOISE', 'None')
        if denoise_val in ('True', 'Standard (FFmpeg)', 'RNNoise', 'SpeexDSP'):
            denoise_val = 'AI (RNNoise)'
        elif denoise_val == 'DeepFilterNet':
            denoise_val = 'AI (DeepFilterNet)'
        elif denoise_val == 'False':
            denoise_val = 'None'
            
        if denoise_val == 'AI (DeepFilterNet)' and not AudioProcessorWrapper.is_available():
            denoise_val = 'AI (RNNoise)'
            
        idx = self.mic_denoise_combo.findData(denoise_val)
        if idx >= 0:
            self.mic_denoise_combo.setCurrentIndex(idx)
        self.mic_denoise_combo.currentIndexChanged.connect(self._on_denoise_changed)

        self.mic_preprocess_combo = QComboBox()
        none_opt = getattr(self.t, 'none_option', "None")
        self.mic_preprocess_combo.addItem(f"{none_opt} (無効化)", "None")
        self.mic_preprocess_combo.addItem("SpeexDSP (HPF有効)", "SpeexDSP")
        
        pre_val = getattr(self.config, 'RECORD_AUDIO_MIC_PREPROCESS', 'SpeexDSP')
        if pre_val == "WebRTC":
            pre_val = "SpeexDSP"
        idx = self.mic_preprocess_combo.findData(pre_val)
        if idx >= 0:
            self.mic_preprocess_combo.setCurrentIndex(idx)
        self.mic_preprocess_combo.currentIndexChanged.connect(self._on_preprocess_changed)
        
        self.preprocess_warning_label = QLabel("※前処理が無効化されているため、低周波ノイズが入りやすくなります")
        self.preprocess_warning_label.setStyleSheet("color: #FFaa00; font-size: 11px; font-weight: bold;")
        self.preprocess_warning_label.setVisible(pre_val == "None")
        
        preprocess_layout = QVBoxLayout()
        preprocess_layout.addWidget(self.mic_preprocess_combo)
        preprocess_layout.addWidget(self.preprocess_warning_label)
        preprocess_layout.setSpacing(2)

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
        self.monitor_warning_label.setVisible(denoise_val in ("AI (RNNoise)", "AI (DeepFilterNet)"))
        
        monitor_layout = QVBoxLayout()
        monitor_layout.addWidget(self.mic_monitor_cb)
        monitor_layout.addWidget(self.monitor_warning_label)
        monitor_layout.setSpacing(2)
        
        audio_layout.addRow(getattr(self.t, 'system_audio_device', "System Audio:"), self.sys_audio_input)
        audio_layout.addRow(self.t.system_gain, sys_gain_layout)
        audio_layout.addRow(self.t.system_level, self.system_volume_meter)
        audio_layout.addRow(self.t.microphone, self.mic_input)
        audio_layout.addRow(self.t.mic_gain, gain_layout)
        audio_layout.addRow(self.t.noise_cancel, self.mic_denoise_combo)
        audio_layout.addRow(getattr(self.t, 'mic_preprocess', 'Pre-process (HPF)'), preprocess_layout)
        audio_layout.addRow(self.t.noise_gate, gate_layout)
        audio_layout.addRow(self.t.mic_level, self.volume_meter)
        audio_layout.addRow("", monitor_layout)
        
        audio_group.setLayout(audio_layout)
        main_layout.addWidget(audio_group)
        
        self.setLayout(main_layout)

    def _on_lightweight_mode_toggled(self, checked):
        if hasattr(self, 'lightweight_hint_label'):
            self.lightweight_hint_label.setVisible(checked)
        if checked:
            if not self.perf_control_cb.isChecked():
                self.perf_control_cb.blockSignals(True)
                self.perf_control_cb.setChecked(True)
                self.perf_control_cb.blockSignals(False)
            # 高フレームレート(120/144)が選択されている場合は60FPSへ最適化
            if self.fps_input.currentText() in ("120", "144"):
                self.fps_input.blockSignals(True)
                self.fps_input.setCurrentText("60")
                self.fps_input.blockSignals(False)
            # 1440pが選択されている場合は1080pへ最適化
            if self.res_input.currentText() == "2560x1440":
                self.res_input.blockSignals(True)
                self.res_input.setCurrentText("1920x1080")
                self.res_input.blockSignals(False)
            # HEVCが選択されている場合は同一GPUベンダーのより軽量なH.264エンコーダへ最適化
            cur_enc = self.encoder_input.currentText()
            if cur_enc.startswith("hevc_"):
                h264_cand = cur_enc.replace("hevc_", "h264_", 1)
                h264_idx = self.encoder_input.findText(h264_cand)
                if h264_idx >= 0:
                    self.encoder_input.blockSignals(True)
                    self.encoder_input.setCurrentIndex(h264_idx)
                    self.encoder_input.blockSignals(False)
            # 高負荷なDeepFilterNet選択時は軽量なAI (RNNoise)へ自動切替
            if self.mic_denoise_combo.currentData() == "AI (DeepFilterNet)":
                rn_idx = self.mic_denoise_combo.findData("AI (RNNoise)")
                if rn_idx >= 0:
                    self.mic_denoise_combo.blockSignals(True)
                    self.mic_denoise_combo.setCurrentIndex(rn_idx)
                    self.mic_denoise_combo.blockSignals(False)
                    if self.monitor_thread and self.mic_monitor_cb.isChecked():
                        self.monitor_thread.set_denoise("AI (RNNoise)")
            if self.mic_monitor_cb.isChecked():
                self.mic_monitor_cb.setChecked(False)
        self._save_settings()

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
            is_listening = self.mic_monitor_cb.isChecked()
            self.monitor_thread.set_denoise(self.mic_denoise_combo.currentData() if is_listening else "None")
            self.monitor_thread.set_monitor_audio(is_listening)

    def _on_preprocess_changed(self, index):
        mode = self.mic_preprocess_combo.currentData()
        self.preprocess_warning_label.setVisible(mode == "None")
        if self.monitor_thread:
            self.monitor_thread.set_preprocess(mode)
        self._save_settings()

    def _on_denoise_changed(self, index):
        mode = self.mic_denoise_combo.currentData()
        self.monitor_warning_label.setVisible(mode in ("AI (RNNoise)", "AI (DeepFilterNet)"))
            
        if mode == "NVIDIA Broadcast":
            found = False
            for i in range(self.mic_input.count()):
                if "NVIDIA Broadcast" in self.mic_input.itemData(i):
                    self.mic_input.setCurrentIndex(i)
                    found = True
                    break
            if not found:
                QMessageBox.warning(self, self.t.nvidia_broadcast_not_found_title, self.t.nvidia_broadcast_not_found_msg)
                idx = self.mic_denoise_combo.findData("AI (RNNoise)")
                if idx >= 0:
                    self.mic_denoise_combo.blockSignals(True)
                    self.mic_denoise_combo.setCurrentIndex(idx)
                    self.mic_denoise_combo.blockSignals(False)
                mode = "AI (RNNoise)"
        
        if self.monitor_thread:
            self.monitor_thread.set_denoise(mode if self.mic_monitor_cb.isChecked() else "None")
        self._save_settings()

    def _on_sys_audio_changed(self):
        self.stop_monitors()
        if self.isVisible():
            self.start_monitors()
        self._save_settings()

    def _on_mic_changed(self):
        self.stop_monitors()
        if self.isVisible():
            self.start_monitors()
        self._save_settings()

    def start_monitors(self):
        mic_name = self.mic_input.currentData()
        if self.monitor_thread is None and mic_name and mic_name != "None":
            gain = self.mic_gain_slider.value() / 100.0
            is_listening = self.mic_monitor_cb.isChecked()
            denoise_mode = self.mic_denoise_combo.currentData() if is_listening else "None"
            gate = self.mic_gate_slider.value() / 100.0
            pre_mode = self.mic_preprocess_combo.currentData()
            self.monitor_thread = MicMonitorThread(mic_name, gain, denoise_mode, gate, pre_mode)
            self.monitor_thread.set_monitor_audio(is_listening)
            self.monitor_thread.level_ready.connect(self.volume_meter.set_level)
            self.monitor_thread.start()
            
        sys_dev = self.sys_audio_input.currentData()
        if self.sys_monitor_thread is None and sys_dev != "None":
            gain = self.system_gain_slider.value() / 100.0
            self.sys_monitor_thread = SystemAudioMonitorThread(gain, device_name=sys_dev)
            self.sys_monitor_thread.level_ready.connect(self.system_volume_meter.set_level)
            self.sys_monitor_thread.start()

    def _retire_thread(self, thread):
        try:
            thread.level_ready.disconnect()
        except Exception:
            pass
        thread.stop()
        if thread.isRunning():
            # ワーカースレッド自身のfinished直結実行による自己GC破棄(qFatal)を防ぐためメインスレッドへ遅延させる
            self._stopping_threads.append(thread)
            thread.finished.connect(
                lambda t=thread: QTimer.singleShot(0, lambda: self._stopping_threads.remove(t) if t in self._stopping_threads else None)
            )

    def stop_monitors(self):
        if self.monitor_thread:
            t = self.monitor_thread
            self.monitor_thread = None
            self._retire_thread(t)
            self.volume_meter.set_level(0.0)
            
        if self.sys_monitor_thread:
            t = self.sys_monitor_thread
            self.sys_monitor_thread = None
            self._retire_thread(t)
            self.system_volume_meter.set_level(0.0)

    def refresh_from_config(self):
        cur_fps = str(getattr(self.config, 'RECORD_FPS', '60'))
        self.fps_input.blockSignals(True)
        if self.fps_input.findText(cur_fps) < 0:
            self.fps_input.addItem(cur_fps)
        self.fps_input.setCurrentText(cur_fps)
        self.fps_input.blockSignals(False)

        cur_enc = str(getattr(self.config, 'RECORD_ENCODER', 'h264_nvenc'))
        self.encoder_input.blockSignals(True)
        if self.encoder_input.findText(cur_enc) >= 0:
            self.encoder_input.setCurrentText(cur_enc)
        self.encoder_input.blockSignals(False)

        cur_res = str(getattr(self.config, 'RECORD_RESOLUTION', '1920x1080'))
        self.res_input.blockSignals(True)
        if "x" in cur_res.lower() and self.res_input.findText(cur_res) < 0:
            self.res_input.addItem(cur_res)
        if self.res_input.findText(cur_res) >= 0:
            self.res_input.setCurrentText(cur_res)
        self.res_input.blockSignals(False)

        cur_vfmt = str(getattr(self.config, 'RECORD_VIDEO_FORMAT', 'ddagrab')).strip().lower()
        self.video_format_input.blockSignals(True)
        vfmt_idx = self.video_format_input.findData(cur_vfmt if cur_vfmt in ("ddagrab", "gdigrab") else "ddagrab")
        if vfmt_idx >= 0:
            self.video_format_input.setCurrentIndex(vfmt_idx)
        self.video_format_input.blockSignals(False)

        lw_checked = bool(getattr(self.config, 'LIGHTWEIGHT_MODE', False))
        self.lightweight_mode_cb.blockSignals(True)
        self.lightweight_mode_cb.setChecked(lw_checked)
        self.lightweight_mode_cb.blockSignals(False)
        if hasattr(self, 'lightweight_hint_label'):
            self.lightweight_hint_label.setVisible(lw_checked)

        self.perf_control_cb.blockSignals(True)
        self.perf_control_cb.setChecked(bool(getattr(self.config, 'AUTO_PERFORMANCE_CONTROL', True)))
        self.perf_control_cb.blockSignals(False)

        cur_sys_dev = str(getattr(self.config, 'RECORD_AUDIO_SYSTEM', 'default')).strip()
        if not cur_sys_dev or cur_sys_dev.lower() == "default":
            cur_sys_dev = "default"
        elif cur_sys_dev.lower() == "none":
            cur_sys_dev = "None"
        self.sys_audio_input.blockSignals(True)
        if self.sys_audio_input.findData(cur_sys_dev) < 0:
            self.sys_audio_input.addItem(cur_sys_dev, cur_sys_dev)
        self.sys_audio_input.setCurrentIndex(self.sys_audio_input.findData(cur_sys_dev))
        self.sys_audio_input.blockSignals(False)

        sys_gain_val = float(getattr(self.config, 'RECORD_AUDIO_SYSTEM_GAIN', '1.0'))
        if sys_gain_val > 10.0:
            sys_gain_val = sys_gain_val / 100.0
        self.system_gain_slider.blockSignals(True)
        self.system_gain_slider.setValue(int(round(sys_gain_val * 100)))
        self.system_gain_label.setText(f"{sys_gain_val:.2f}x")
        self.system_gain_slider.blockSignals(False)

        cur_mic = str(getattr(self.config, 'RECORD_AUDIO_MIC', '')).strip()
        if cur_mic.lower() == "none":
            target_mic = "None"
        elif not cur_mic or cur_mic.lower() == "default":
            target_mic = "default"
        else:
            target_mic = cur_mic
        self.mic_input.blockSignals(True)
        if self.mic_input.findData(target_mic) < 0:
            self.mic_input.addItem(target_mic, target_mic)
        self.mic_input.setCurrentIndex(self.mic_input.findData(target_mic))
        self.mic_input.blockSignals(False)

        mic_gain_val = float(getattr(self.config, 'RECORD_AUDIO_MIC_GAIN', '1.0'))
        if mic_gain_val > 10.0:
            mic_gain_val = mic_gain_val / 100.0
        self.mic_gain_slider.blockSignals(True)
        self.mic_gain_slider.setValue(int(round(mic_gain_val * 100)))
        self.mic_gain_label.setText(f"{mic_gain_val:.2f}x")
        self.mic_gain_slider.blockSignals(False)

        gate_val = int(float(getattr(self.config, 'RECORD_AUDIO_MIC_NOISE_GATE', '0')))
        self.mic_gate_slider.blockSignals(True)
        self.mic_gate_slider.setValue(gate_val)
        self.mic_gate_label.setText(f"{gate_val}%")
        self.volume_meter.set_gate_threshold(gate_val / 100.0)
        self.mic_gate_slider.blockSignals(False)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_from_config()

    def _save_settings(self):
        self.config.RECORD_FPS = self.fps_input.currentText()
        self.config.RECORD_ENCODER = self.encoder_input.currentText()
        self.config.RECORD_RESOLUTION = self.res_input.currentText()
        self.config.RECORD_VIDEO_FORMAT = self.video_format_input.currentData() or "ddagrab"
        self.config.LIGHTWEIGHT_MODE = self.lightweight_mode_cb.isChecked()
        self.config.AUTO_PERFORMANCE_CONTROL = self.perf_control_cb.isChecked()
        
        self.config.RECORD_AUDIO_SYSTEM = self.sys_audio_input.currentData() or "default"
        self.config.RECORD_AUDIO_SYSTEM_GAIN = str(self.system_gain_slider.value() / 100.0)
        mic_val = self.mic_input.currentData()
        if mic_val == "None":
            self.config.RECORD_AUDIO_MIC = "None"
        elif mic_val == "default":
            self.config.RECORD_AUDIO_MIC = "default"
        else:
            self.config.RECORD_AUDIO_MIC = mic_val or ""
        self.config.RECORD_AUDIO_MIC_GAIN = str(self.mic_gain_slider.value() / 100.0)
        self.config.RECORD_AUDIO_MIC_NOISE_GATE = str(self.mic_gate_slider.value())
        self.config.RECORD_AUDIO_MIC_DENOISE = self.mic_denoise_combo.currentData()
        self.config.RECORD_AUDIO_MIC_PREPROCESS = self.mic_preprocess_combo.currentData()
        
        self.config.save()
