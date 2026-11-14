"""Module: @role: 録音・システム音声のデバイス選択、ゲイン調整、ノイズ抑制、レベルメーター監視を担うUIモジュール"""
from typing import Optional
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from recorder.audio_processor_wrapper import AudioProcessorWrapper
from ui.audio_monitor_threads import MicMonitorThread, SystemAudioMonitorThread
from ui.settings_utils import HoverWheelComboBox
from ui.volume_meter import VolumeMeter


class AudioSettingsWidget(QWidget):
    def __init__(self, config, t, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        self.t = t

        self.monitor_thread: Optional[MicMonitorThread] = None
        self.sys_monitor_thread: Optional[SystemAudioMonitorThread] = None
        self._stopping_threads: list = []

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        audio_group = QGroupBox(self.t.audio_settings)
        audio_layout = QFormLayout()
        audio_layout.setContentsMargins(16, 18, 16, 14)
        audio_layout.setHorizontalSpacing(20)
        audio_layout.setVerticalSpacing(14)
        audio_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        audio_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.sys_audio_input = HoverWheelComboBox()
        self.sys_audio_input.setCursor(Qt.CursorShape.PointingHandCursor)
        default_label = getattr(self.t, "default_device", "Default")
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
        cur_sys_dev = str(getattr(self.config, "RECORD_AUDIO_SYSTEM", "default")).strip()
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
        self.system_gain_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.system_gain_slider.setRange(0, 300)
        sys_gain_val = float(getattr(self.config, "RECORD_AUDIO_SYSTEM_GAIN", "1.0"))
        if sys_gain_val > 10.0:
            sys_gain_val = sys_gain_val / 100.0
        self.system_gain_slider.setValue(int(round(sys_gain_val * 100)))
        self.system_gain_slider.valueChanged.connect(self._on_system_gain_changed)

        self.system_gain_label = QLabel(f"{sys_gain_val:.2f}x")
        self.system_gain_label.setMinimumWidth(56)
        self.system_gain_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.system_gain_label.setStyleSheet("font-weight: bold; color: #FFFFFF;")

        sys_gain_layout = QHBoxLayout()
        sys_gain_layout.setSpacing(10)
        sys_gain_layout.addWidget(self.system_gain_slider, 1)
        sys_gain_layout.addWidget(self.system_gain_label)

        self.system_volume_meter = VolumeMeter()
        self.system_volume_meter.setMinimumHeight(20)

        self.mic_input = HoverWheelComboBox()
        self.mic_input.setCursor(Qt.CursorShape.PointingHandCursor)
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

        cur_mic = str(getattr(self.config, "RECORD_AUDIO_MIC", "")).strip()
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
        self.mic_gain_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic_gain_slider.setRange(0, 300)
        gain_val = float(getattr(self.config, "RECORD_AUDIO_MIC_GAIN", "1.0"))
        if gain_val > 10.0:
            gain_val = gain_val / 100.0
        self.mic_gain_slider.setValue(int(round(gain_val * 100)))
        self.mic_gain_slider.valueChanged.connect(self._on_gain_changed)

        self.mic_gain_label = QLabel(f"{gain_val:.2f}x")
        self.mic_gain_label.setMinimumWidth(56)
        self.mic_gain_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.mic_gain_label.setStyleSheet("font-weight: bold; color: #FFFFFF;")

        gain_layout = QHBoxLayout()
        gain_layout.setSpacing(10)
        gain_layout.addWidget(self.mic_gain_slider, 1)
        gain_layout.addWidget(self.mic_gain_label)

        self.mic_denoise_combo = HoverWheelComboBox()
        self.mic_denoise_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic_denoise_combo.addItem(self.t.none_option, "None")
        if AudioProcessorWrapper.is_available():
            self.mic_denoise_combo.addItem("AI (DeepFilterNet)", "AI (DeepFilterNet)")
        self.mic_denoise_combo.addItem("AI (RNNoise)", "AI (RNNoise)")
        self.mic_denoise_combo.addItem("NVIDIA Broadcast", "NVIDIA Broadcast")

        denoise_val = getattr(self.config, "RECORD_AUDIO_MIC_DENOISE", "None")
        if denoise_val in ("True", "Standard (FFmpeg)", "RNNoise", "SpeexDSP"):
            denoise_val = "AI (RNNoise)"
        elif denoise_val == "DeepFilterNet":
            denoise_val = "AI (DeepFilterNet)"
        elif denoise_val == "False":
            denoise_val = "None"
        if denoise_val == "AI (DeepFilterNet)" and not AudioProcessorWrapper.is_available():
            denoise_val = "AI (RNNoise)"

        idx = self.mic_denoise_combo.findData(denoise_val)
        if idx >= 0:
            self.mic_denoise_combo.setCurrentIndex(idx)
        self.mic_denoise_combo.currentIndexChanged.connect(self._on_denoise_changed)

        self.mic_preprocess_combo = HoverWheelComboBox()
        self.mic_preprocess_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        none_opt = getattr(self.t, "none_option", "None")
        self.mic_preprocess_combo.addItem(f"{none_opt} (無効化)", "None")
        self.mic_preprocess_combo.addItem("SpeexDSP (HPF有効)", "SpeexDSP")

        pre_val = getattr(self.config, "RECORD_AUDIO_MIC_PREPROCESS", "SpeexDSP")
        if pre_val == "WebRTC":
            pre_val = "SpeexDSP"
        idx = self.mic_preprocess_combo.findData(pre_val)
        if idx >= 0:
            self.mic_preprocess_combo.setCurrentIndex(idx)
        self.mic_preprocess_combo.currentIndexChanged.connect(self._on_preprocess_changed)

        self.preprocess_warning_label = QLabel("※前処理が無効化されているため、低周波ノイズが入りやすくなります")
        self.preprocess_warning_label.setWordWrap(True)
        self.preprocess_warning_label.setStyleSheet("color: #FFB400; font-size: 13px; font-weight: bold; padding: 2px 0;")
        self.preprocess_warning_label.setVisible(pre_val == "None")

        preprocess_layout = QVBoxLayout()
        preprocess_layout.setContentsMargins(0, 0, 0, 0)
        preprocess_layout.setSpacing(4)
        preprocess_layout.addWidget(self.mic_preprocess_combo)
        preprocess_layout.addWidget(self.preprocess_warning_label)

        self.mic_gate_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_gate_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic_gate_slider.setRange(0, 100)
        gate_val = int(float(getattr(self.config, "RECORD_AUDIO_MIC_NOISE_GATE", "0")))
        self.mic_gate_slider.setValue(gate_val)
        self.mic_gate_slider.valueChanged.connect(self._on_gate_changed)

        self.mic_gate_label = QLabel(f"{gate_val}%")
        self.mic_gate_label.setMinimumWidth(56)
        self.mic_gate_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.mic_gate_label.setStyleSheet("font-weight: bold; color: #FFFFFF;")

        gate_layout = QHBoxLayout()
        gate_layout.setSpacing(10)
        gate_layout.addWidget(self.mic_gate_slider, 1)
        gate_layout.addWidget(self.mic_gate_label)

        self.volume_meter = VolumeMeter()
        self.volume_meter.setMinimumHeight(20)
        self.volume_meter.set_gate_threshold(gate_val / 100.0)

        self.mic_monitor_cb = QCheckBox(self.t.listen_to_mic)
        self.mic_monitor_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic_monitor_cb.setChecked(False)
        self.mic_monitor_cb.stateChanged.connect(self._on_monitor_changed)

        self.monitor_warning_label = QLabel(self.t.monitor_warning)
        self.monitor_warning_label.setWordWrap(True)
        self.monitor_warning_label.setStyleSheet("color: #B8C4D0; font-size: 13px; padding-left: 26px;")
        self.monitor_warning_label.setVisible(denoise_val in ("AI (RNNoise)", "AI (DeepFilterNet)"))

        monitor_layout = QVBoxLayout()
        monitor_layout.setContentsMargins(0, 0, 0, 0)
        monitor_layout.setSpacing(4)
        monitor_layout.addWidget(self.mic_monitor_cb)
        monitor_layout.addWidget(self.monitor_warning_label)

        audio_layout.addRow(getattr(self.t, "system_audio_device", "System Audio:"), self.sys_audio_input)
        audio_layout.addRow(self.t.system_gain, sys_gain_layout)
        audio_layout.addRow(self.t.system_level, self.system_volume_meter)
        audio_layout.addRow(self.t.microphone, self.mic_input)
        audio_layout.addRow(self.t.mic_gain, gain_layout)
        audio_layout.addRow(self.t.noise_cancel, self.mic_denoise_combo)
        audio_layout.addRow(getattr(self.t, "mic_preprocess", "Pre-process (HPF)"), preprocess_layout)
        audio_layout.addRow(self.t.noise_gate, gate_layout)
        audio_layout.addRow(self.t.mic_level, self.volume_meter)
        audio_layout.addRow("", monitor_layout)

        audio_group.setLayout(audio_layout)
        outer_layout.addWidget(audio_group)

    def apply_lightweight_defaults(self) -> None:
        if self.mic_denoise_combo.currentData() == "AI (DeepFilterNet)":
            rn_idx = self.mic_denoise_combo.findData("AI (RNNoise)")
            if rn_idx >= 0:
                self.mic_denoise_combo.blockSignals(True)
                self.mic_denoise_combo.setCurrentIndex(rn_idx)
                self.mic_denoise_combo.blockSignals(False)
                if self.monitor_thread:
                    self.monitor_thread.set_denoise("AI (RNNoise)")
        if self.mic_monitor_cb.isChecked():
            self.mic_monitor_cb.setChecked(False)
        self._save_audio_settings()

    def _on_system_gain_changed(self, value: int) -> None:
        gain = value / 100.0
        self.system_gain_label.setText(f"{gain:.2f}x")
        if self.sys_monitor_thread:
            self.sys_monitor_thread.set_gain(gain)
        self._save_audio_settings()

    def _on_gain_changed(self, value: int) -> None:
        gain = value / 100.0
        self.mic_gain_label.setText(f"{gain:.2f}x")
        if self.monitor_thread:
            self.monitor_thread.set_gain(gain)
        self._save_audio_settings()

    def _on_gate_changed(self, value: int) -> None:
        self.mic_gate_label.setText(f"{value}%")
        self.volume_meter.set_gate_threshold(value / 100.0)
        if self.monitor_thread:
            self.monitor_thread.set_gate_threshold(value / 100.0)
        self._save_audio_settings()

    def _on_monitor_changed(self, state: int) -> None:
        is_listening = self.mic_monitor_cb.isChecked()
        if is_listening and (self.monitor_thread is None or not self.monitor_thread.isRunning()) and self.isVisible():
            self.start_monitors()
        if self.monitor_thread:
            self.monitor_thread.set_denoise(self.mic_denoise_combo.currentData())
            self.monitor_thread.set_monitor_audio(is_listening)

    def _on_preprocess_changed(self, index: int) -> None:
        mode = self.mic_preprocess_combo.currentData()
        self.preprocess_warning_label.setVisible(mode == "None")
        if self.monitor_thread:
            self.monitor_thread.set_preprocess(mode)
        self._save_audio_settings()

    def _on_denoise_changed(self, index: int) -> None:
        mode = self.mic_denoise_combo.currentData()
        self.monitor_warning_label.setVisible(mode in ("AI (RNNoise)", "AI (DeepFilterNet)"))

        if mode == "NVIDIA Broadcast":
            found = False
            for i in range(self.mic_input.count()):
                if "NVIDIA Broadcast" in str(self.mic_input.itemData(i)):
                    self.mic_input.setCurrentIndex(i)
                    found = True
                    break
            if not found:
                self.mic_denoise_combo.hidePopup()
                QMessageBox.warning(
                    self, self.t.nvidia_broadcast_not_found_title, self.t.nvidia_broadcast_not_found_msg
                )
                idx = self.mic_denoise_combo.findData("AI (RNNoise)")
                if idx >= 0:
                    self.mic_denoise_combo.blockSignals(True)
                    self.mic_denoise_combo.setCurrentIndex(idx)
                    self.mic_denoise_combo.blockSignals(False)
                mode = "AI (RNNoise)"

        if self.monitor_thread:
            self.monitor_thread.set_denoise(mode)
        self._save_audio_settings()

    def _on_sys_audio_changed(self) -> None:
        self.stop_monitors()
        if self.isVisible():
            self.start_monitors()
        self._save_audio_settings()

    def _on_mic_changed(self) -> None:
        self.stop_monitors()
        if self.isVisible():
            self.start_monitors()
        self._save_audio_settings()

    def start_monitors(self) -> None:
        if self.monitor_thread is not None and not self.monitor_thread.isRunning():
            self._retire_thread(self.monitor_thread)
            self.monitor_thread = None

        mic_name = self.mic_input.currentData()
        if self.monitor_thread is None and mic_name and mic_name != "None":
            gain = self.mic_gain_slider.value() / 100.0
            is_listening = self.mic_monitor_cb.isChecked()
            denoise_mode = self.mic_denoise_combo.currentData()
            gate = self.mic_gate_slider.value() / 100.0
            pre_mode = self.mic_preprocess_combo.currentData()
            self.monitor_thread = MicMonitorThread(mic_name, gain, denoise_mode, gate, pre_mode)
            self.monitor_thread.set_monitor_audio(is_listening)
            self.monitor_thread.level_ready.connect(self.volume_meter.set_level)
            self.monitor_thread.start()

        if self.sys_monitor_thread is not None and not self.sys_monitor_thread.isRunning():
            self._retire_thread(self.sys_monitor_thread)
            self.sys_monitor_thread = None

        sys_dev = self.sys_audio_input.currentData()
        if self.sys_monitor_thread is None and sys_dev != "None":
            gain = self.system_gain_slider.value() / 100.0
            self.sys_monitor_thread = SystemAudioMonitorThread(gain, device_name=sys_dev)
            self.sys_monitor_thread.level_ready.connect(self.system_volume_meter.set_level)
            self.sys_monitor_thread.start()

    def _retire_thread(self, thread) -> None:
        try:
            thread.level_ready.disconnect()
        except Exception:
            pass
        thread.stop()
        if thread.isRunning():
            # ワーカースレッド自身のfinished直結実行による自己GC破棄(qFatal)を防ぐためメインスレッドへ遅延させる
            self._stopping_threads.append(thread)
            thread.finished.connect(
                lambda t=thread: QTimer.singleShot(
                    0, lambda: self._stopping_threads.remove(t) if t in self._stopping_threads else None
                )
            )

    def stop_monitors(self) -> None:
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

    def refresh_from_config(self) -> None:
        cur_sys_dev = str(getattr(self.config, "RECORD_AUDIO_SYSTEM", "default")).strip()
        if not cur_sys_dev or cur_sys_dev.lower() == "default":
            cur_sys_dev = "default"
        elif cur_sys_dev.lower() == "none":
            cur_sys_dev = "None"
        self.sys_audio_input.blockSignals(True)
        if self.sys_audio_input.findData(cur_sys_dev) < 0:
            self.sys_audio_input.addItem(cur_sys_dev, cur_sys_dev)
        self.sys_audio_input.setCurrentIndex(self.sys_audio_input.findData(cur_sys_dev))
        self.sys_audio_input.blockSignals(False)

        sys_gain_val = float(getattr(self.config, "RECORD_AUDIO_SYSTEM_GAIN", "1.0"))
        if sys_gain_val > 10.0:
            sys_gain_val = sys_gain_val / 100.0
        self.system_gain_slider.blockSignals(True)
        self.system_gain_slider.setValue(int(round(sys_gain_val * 100)))
        self.system_gain_label.setText(f"{sys_gain_val:.2f}x")
        self.system_gain_slider.blockSignals(False)

        cur_mic = str(getattr(self.config, "RECORD_AUDIO_MIC", "")).strip()
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

        mic_gain_val = float(getattr(self.config, "RECORD_AUDIO_MIC_GAIN", "1.0"))
        if mic_gain_val > 10.0:
            mic_gain_val = mic_gain_val / 100.0
        self.mic_gain_slider.blockSignals(True)
        self.mic_gain_slider.setValue(int(round(mic_gain_val * 100)))
        self.mic_gain_label.setText(f"{mic_gain_val:.2f}x")
        self.mic_gain_slider.blockSignals(False)

        gate_val = int(float(getattr(self.config, "RECORD_AUDIO_MIC_NOISE_GATE", "0")))
        self.mic_gate_slider.blockSignals(True)
        self.mic_gate_slider.setValue(gate_val)
        self.mic_gate_label.setText(f"{gate_val}%")
        self.volume_meter.set_gate_threshold(gate_val / 100.0)
        self.mic_gate_slider.blockSignals(False)

        denoise_val = getattr(self.config, "RECORD_AUDIO_MIC_DENOISE", "None")
        if denoise_val in ("True", "Standard (FFmpeg)", "RNNoise", "SpeexDSP"):
            denoise_val = "AI (RNNoise)"
        elif denoise_val == "DeepFilterNet":
            denoise_val = "AI (DeepFilterNet)"
        elif denoise_val == "False":
            denoise_val = "None"
        self.mic_denoise_combo.blockSignals(True)
        dn_idx = self.mic_denoise_combo.findData(denoise_val)
        if dn_idx >= 0:
            self.mic_denoise_combo.setCurrentIndex(dn_idx)
        self.mic_denoise_combo.blockSignals(False)
        self.monitor_warning_label.setVisible(denoise_val in ("AI (RNNoise)", "AI (DeepFilterNet)"))

        pre_val = getattr(self.config, "RECORD_AUDIO_MIC_PREPROCESS", "SpeexDSP")
        if pre_val == "WebRTC":
            pre_val = "SpeexDSP"
        self.mic_preprocess_combo.blockSignals(True)
        pre_idx = self.mic_preprocess_combo.findData(pre_val)
        if pre_idx >= 0:
            self.mic_preprocess_combo.setCurrentIndex(pre_idx)
        self.mic_preprocess_combo.blockSignals(False)
        self.preprocess_warning_label.setVisible(pre_val == "None")

        if self.sys_monitor_thread:
            self.sys_monitor_thread.set_gain(self.system_gain_slider.value() / 100.0)
        if self.monitor_thread:
            self.monitor_thread.set_gain(self.mic_gain_slider.value() / 100.0)
            self.monitor_thread.set_gate_threshold(self.mic_gate_slider.value() / 100.0)
            self.monitor_thread.set_denoise(self.mic_denoise_combo.currentData())
            self.monitor_thread.set_preprocess(self.mic_preprocess_combo.currentData())

    def _save_audio_settings(self) -> None:
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
