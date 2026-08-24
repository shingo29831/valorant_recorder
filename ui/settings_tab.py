from PyQt6.QtWidgets import (QWidget, QFormLayout, QLineEdit, QComboBox, 
                             QPushButton, QMessageBox, QVBoxLayout, QLabel, 
                             QHBoxLayout, QSlider, QCheckBox, QFileDialog)
from PyQt6.QtCore import pyqtSignal, Qt, QThread
from PyQt6.QtGui import QPainter, QColor
from core.config import Config
import numpy as np
import os
import shutil

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

class SystemAudioMonitorThread(QThread):
    level_ready = pyqtSignal(float)

    def __init__(self, gain):
        super().__init__()
        self.gain = gain
        self.running = True

    def set_gain(self, gain):
        self.gain = gain

    def process_audio(self, data):
        data = data * self.gain
        peak = np.max(np.abs(data))
        level = min(1.0, peak ** 0.5)
        self.level_ready.emit(float(level))
        return data

    def run(self):
        import warnings
        warnings.filterwarnings("ignore", message=".*data discontinuity.*")
        warnings.filterwarnings("ignore", module=".*soundcard.*")
        try:
            import soundcard as sc
            warnings.simplefilter("ignore", category=sc.SoundcardRuntimeWarning)
            
            speaker = sc.default_speaker()
            spk_mic = sc.get_microphone(speaker.id, include_loopback=True)
            
            with spk_mic.recorder(samplerate=48000, channels=2) as recorder:
                while self.running:
                    data = recorder.record(numframes=2400)
                    # ステレオの場合は平均をとってモノラルにダウンミックスしてレベル計算
                    data_mono = data.mean(axis=1, keepdims=True)
                    self.process_audio(data_mono)
        except Exception as e:
            import traceback
            import os
            print("\n=== SystemAudioMonitorThread Error ===")
            traceback.print_exc()
            print("======================================\n")
            log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sys_audio_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"SystemAudioMonitorThread error:\n{traceback.format_exc()}\n")

    def stop(self):
        self.running = False
        if not self.wait(2000):
            import os, datetime
            log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "thread_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] SystemAudioMonitorThread wait timed out.\n")

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
        import warnings
        warnings.filterwarnings("ignore", message=".*data discontinuity.*")
        warnings.filterwarnings("ignore", module=".*soundcard.*")
        try:
            import soundcard as sc
            
            # sc.SoundcardRuntimeWarning も明示的に無視する
            warnings.simplefilter("ignore", category=sc.SoundcardRuntimeWarning)
            
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
        if not self.wait(2000):
            import os, datetime
            log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "thread_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] MicMonitorThread wait timed out.\n")

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
        
        self.system_gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.system_gain_slider.setRange(0, 300)
        sys_gain_val = float(getattr(self.config, 'RECORD_AUDIO_SYSTEM_GAIN', '1.0'))
        self.system_gain_slider.setValue(int(sys_gain_val * 100))
        
        self.system_gain_label = QLabel(f"{sys_gain_val:.2f}x")
        self.system_gain_label.setFixedWidth(40)
        
        sys_gain_layout = QHBoxLayout()
        sys_gain_layout.addWidget(self.system_gain_slider)
        sys_gain_layout.addWidget(self.system_gain_label)
        
        self.system_volume_meter = VolumeMeter()
        
        self.save_dir_input = QLineEdit(self.config.SAVE_DIR)
        self.save_dir_input.setReadOnly(True)
        self.save_dir_btn = QPushButton("Browse")
        self.save_dir_btn.clicked.connect(self._browse_save_dir)
        
        save_dir_layout = QHBoxLayout()
        save_dir_layout.addWidget(self.save_dir_input)
        save_dir_layout.addWidget(self.save_dir_btn)

        self.riot_id_input = QLineEdit(self.config.RIOT_ID)
        self.tag_line_input = QLineEdit(self.config.TAG_LINE)
        self.api_key_input = QLineEdit(self.config.API_KEY)
        
        form_layout.addRow("Save Directory:", save_dir_layout)
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
        
        form_layout.addRow("System Gain:", sys_gain_layout)
        form_layout.addRow("System Level:", self.system_volume_meter)
        form_layout.addRow("Microphone:", self.mic_input)
        form_layout.addRow("Mic Gain:", gain_layout)
        form_layout.addRow("Noise Cancel:", self.mic_denoise_combo)
        form_layout.addRow("Noise Gate:", gate_layout)
        form_layout.addRow("Mic Level:", self.volume_meter)
        form_layout.addRow("", monitor_layout)
        
        main_layout.addLayout(form_layout)
        
        self.system_gain_slider.valueChanged.connect(self._on_system_gain_changed)
        self.mic_gain_slider.valueChanged.connect(self._on_gain_changed)
        self.mic_gate_slider.valueChanged.connect(self._on_gate_changed)
        self.mic_denoise_combo.currentIndexChanged.connect(self._on_denoise_changed)
        self.mic_input.currentIndexChanged.connect(self._on_mic_changed)
        self.mic_monitor_cb.stateChanged.connect(self._on_monitor_changed)
        self.monitor_thread = None
        self.sys_monitor_thread = None
        
        self.riot_id_input.textChanged.connect(self._save_settings)
        self.tag_line_input.textChanged.connect(self._save_settings)
        self.api_key_input.textChanged.connect(self._save_settings)
        self.fps_input.currentTextChanged.connect(self._save_settings)
        self.encoder_input.currentTextChanged.connect(self._save_settings)
        self.res_input.currentTextChanged.connect(self._save_settings)
        
        main_layout.addStretch()
        self.setLayout(main_layout)

    def _browse_save_dir(self):
        import datetime
        import traceback
        log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dialog_debug.log")
        
        def write_log(msg):
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] {msg}\n")

        write_log("--- Starting _browse_save_dir ---")
        try:
            write_log("Stopping monitors to save resources...")
            self._stop_mic_monitor()
            self._stop_system_monitor()
            write_log("Monitors stopped.")

            write_log("Opening native QFileDialog...")
            selected_dir = QFileDialog.getExistingDirectory(
                self, 
                "Select Directory",
                self.config.SAVE_DIR
            )
            write_log(f"QFileDialog closed. Selected: {selected_dir}")

            if not selected_dir:
                write_log("No directory selected. Aborting.")
                return
            
            selected_dir = selected_dir.replace('\\', '/')
            if not selected_dir.endswith('/valorant_records'):
                new_save_dir = f"{selected_dir}/valorant_records"
            else:
                new_save_dir = selected_dir

            old_save_dir = self.config.SAVE_DIR.replace('\\', '/')
            write_log(f"Old dir: {old_save_dir}, New dir: {new_save_dir}")

            if new_save_dir == old_save_dir:
                write_log("New directory is the same as old directory. Aborting.")
                return

            if os.path.exists(old_save_dir):
                files_to_copy = [f for f in os.listdir(old_save_dir) if os.path.isfile(os.path.join(old_save_dir, f))]
                
                if files_to_copy:
                    reply_move = QMessageBox.question(
                        self,
                        "Move Files?",
                        f"Do you want to move existing recordings to the new location?\n\nFrom: {old_save_dir}\nTo: {new_save_dir}",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    
                    if reply_move == QMessageBox.StandardButton.Yes:
                        write_log("User chose to move files. Starting copy process.")
                        os.makedirs(new_save_dir, exist_ok=True)
                        
                        from PyQt6.QtWidgets import QProgressDialog, QApplication
                        
                        total_bytes = sum(os.path.getsize(os.path.join(old_save_dir, f)) for f in files_to_copy)
                        copied_bytes = 0
                        
                        progress = QProgressDialog("Copying files...", "Cancel", 0, 100, self)
                        progress.setWindowModality(Qt.WindowModality.WindowModal)
                        progress.setMinimumDuration(0)
                        progress.show()

                        cancel_copy = False
                        copied_files = []
                        for f in files_to_copy:
                            if cancel_copy:
                                break
                                
                            src = os.path.join(old_save_dir, f)
                            dst = os.path.join(new_save_dir, f)
                            
                            try:
                                progress.setLabelText(f"Copying: {f}")
                                QApplication.processEvents()
                                
                                length = 16 * 1024 * 1024 # 16MB chunks
                                with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
                                    while True:
                                        if progress.wasCanceled():
                                            write_log("Copy process canceled by user.")
                                            cancel_copy = True
                                            break
                                            
                                        buf = fsrc.read(length)
                                        if not buf:
                                            break
                                        fdst.write(buf)
                                        copied_bytes += len(buf)
                                        
                                        if total_bytes > 0:
                                            percent = int((copied_bytes / total_bytes) * 100)
                                            progress.setValue(percent)
                                        QApplication.processEvents()
                                        
                                if not cancel_copy:
                                    shutil.copystat(src, dst)
                                    copied_files.append(dst)
                                    
                            except Exception as e:
                                write_log(f"Failed to copy {f}: {e}")
                        
                        if cancel_copy:
                            write_log("Cleaning up copied files due to cancellation.")
                            for dst_file in copied_files:
                                try:
                                    if os.path.exists(dst_file):
                                        os.remove(dst_file)
                                except Exception as e:
                                    write_log(f"Failed to remove {dst_file}: {e}")
                            
                            # 途中でキャンセルされたファイルも削除
                            if 'dst' in locals() and os.path.exists(dst):
                                try:
                                    os.remove(dst)
                                except Exception:
                                    pass
                                    
                            try:
                                if not os.listdir(new_save_dir):
                                    os.rmdir(new_save_dir)
                            except Exception:
                                pass
                            
                            write_log("Cancellation cleanup finished. Aborting directory change.")
                            return

                        progress.setValue(100)
                        write_log("Copy finished. Prompting for deletion.")
                        reply1 = QMessageBox.question(
                            self, 
                            "Delete Original Files?", 
                            f"Videos have been copied to the new location.\nDo you want to delete the original files in:\n{old_save_dir}?",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                        )
                        
                        if reply1 == QMessageBox.StandardButton.Yes:
                            reply2 = QMessageBox.question(
                                self,
                                "Confirm Deletion",
                                "Are you absolutely sure you want to delete the original files? This action cannot be undone.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                            )
                            if reply2 == QMessageBox.StandardButton.Yes:
                                write_log("Deletion confirmed. Deleting files.")
                                for f in files_to_copy:
                                    try:
                                        os.remove(os.path.join(old_save_dir, f))
                                    except Exception as e:
                                        write_log(f"Failed to delete {f}: {e}")
                                try:
                                    if not os.listdir(old_save_dir):
                                        os.rmdir(old_save_dir)
                                        write_log("Old directory removed.")
                                except Exception as e:
                                    write_log(f"Failed to remove old directory: {e}")
                    else:
                        write_log("User chose not to move files.")
                        os.makedirs(new_save_dir, exist_ok=True)
                else:
                    write_log("No files to copy. Creating new directory.")
                    os.makedirs(new_save_dir, exist_ok=True)
            else:
                write_log("Old directory does not exist. Creating new directory.")
                os.makedirs(new_save_dir, exist_ok=True)

            self.save_dir_input.setText(new_save_dir)
            self.config.SAVE_DIR = new_save_dir
            self.config.save()
            write_log("--- _browse_save_dir completed successfully ---")

        except Exception as e:
            write_log(f"Exception in _browse_save_dir: {e}\n{traceback.format_exc()}")
        finally:
            write_log("Restarting monitors...")
            self._start_mic_monitor()
            self._start_system_monitor()
            write_log("Monitors restarted.")

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

    def _start_system_monitor(self):
        if self.sys_monitor_thread is not None:
            return
        gain = self.system_gain_slider.value() / 100.0
        self.sys_monitor_thread = SystemAudioMonitorThread(gain)
        self.sys_monitor_thread.level_ready.connect(self.system_volume_meter.set_level)
        self.sys_monitor_thread.start()

    def _stop_system_monitor(self):
        if self.sys_monitor_thread:
            self.sys_monitor_thread.stop()
            self.sys_monitor_thread = None
            self.system_volume_meter.set_level(0.0)

    def showEvent(self, event):
        super().showEvent(event)
        self._start_mic_monitor()
        self._start_system_monitor()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._stop_mic_monitor()
        self._stop_system_monitor()

    def _save_settings(self, *args):
        self.config.SAVE_DIR = self.save_dir_input.text()
        self.config.RIOT_ID = self.riot_id_input.text()
        self.config.TAG_LINE = self.tag_line_input.text()
        self.config.API_KEY = self.api_key_input.text()
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