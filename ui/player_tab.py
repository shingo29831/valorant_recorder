import os
import json
import subprocess
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QListWidget, 
                             QPushButton, QLabel, QSlider, QListWidgetItem, QStackedWidget)
from PyQt6.QtCore import Qt, QUrl, QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from core.config import Config

class PlayerTab(QWidget):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        # 画面切り替え用のスタックドウィジェット
        self.stacked_widget = QStackedWidget()
        self.layout.addWidget(self.stacked_widget)
        self.setLayout(self.layout)
        
        self.setup_list_page()
        self.setup_player_page()
        
        self.stacked_widget.addWidget(self.list_page)
        self.stacked_widget.addWidget(self.player_page)
        
        self.refresh_list()

    def setup_list_page(self):
        self.list_page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        
        header_layout = QHBoxLayout()
        title_label = QLabel("MATCH RECORDINGS")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #FF4655;")
        
        refresh_btn = QPushButton("REFRESH LIST")
        refresh_btn.setFixedWidth(150)
        refresh_btn.clicked.connect(self.refresh_list)
        
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(refresh_btn)
        
        self.record_list = QListWidget()
        self.record_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.record_list.setIconSize(QSize(240, 135))
        self.record_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.record_list.setSpacing(15)
        self.record_list.setGridSize(QSize(260, 180))
        self.record_list.setWordWrap(True)
        self.record_list.itemClicked.connect(self.load_recording)
        
        layout.addLayout(header_layout)
        layout.addWidget(self.record_list)
        self.list_page.setLayout(layout)

    def setup_player_page(self):
        self.player_page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        
        back_btn = QPushButton("← BACK TO LIST")
        back_btn.setFixedWidth(150)
        back_btn.clicked.connect(self.show_list_page)
        layout.addWidget(back_btn)
        
        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background-color: #000000;")
        
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderMoved.connect(self.set_position)
        self.media_player.positionChanged.connect(self.position_changed)
        self.media_player.durationChanged.connect(self.duration_changed)
        self.media_player.errorOccurred.connect(self.handle_media_error)
        
        controls_layout = QHBoxLayout()
        self.play_btn = QPushButton("PLAY")
        self.play_btn.setFixedWidth(100)
        self.play_btn.clicked.connect(self.toggle_play)
        controls_layout.addWidget(self.play_btn)
        controls_layout.addWidget(self.slider)
        
        events_label = QLabel("MATCH EVENTS (Click to seek) / ERRORS")
        events_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        
        self.log_list = QListWidget()
        self.log_list.setFixedHeight(150)
        self.log_list.itemClicked.connect(self.seek_to_log)
        
        layout.addWidget(self.video_widget, stretch=1)
        layout.addLayout(controls_layout)
        layout.addWidget(events_label)
        layout.addWidget(self.log_list)
        
        self.player_page.setLayout(layout)

    def show_list_page(self):
        self.media_player.stop()
        self.stacked_widget.setCurrentWidget(self.list_page)

    def refresh_list(self):
        self.record_list.clear()
        if not os.path.exists(self.config.SAVE_DIR):
            return
            
        for f in sorted(os.listdir(self.config.SAVE_DIR), reverse=True):
            if f.endswith(".json"):
                json_path = os.path.join(self.config.SAVE_DIR, f)
                try:
                    with open(json_path, 'r', encoding='utf-8') as jf:
                        data = json.load(jf)
                    
                    video_path = data.get("local_video_path")
                    
                    # フォールバック: JSON内のパスが無効な場合、同階層の同名動画を探す
                    if not video_path or not os.path.exists(video_path):
                        base_name = os.path.splitext(f)[0]
                        for ext in ['.mkv', '.mp4', '.avi']:
                            fallback = os.path.join(self.config.SAVE_DIR, base_name + ext)
                            if os.path.exists(fallback):
                                video_path = fallback
                                break

                    item = QListWidgetItem(f)
                    
                    if video_path and os.path.exists(video_path):
                        thumb_path = os.path.join(self.config.SAVE_DIR, f.replace('.json', '.jpg'))
                        if not os.path.exists(thumb_path):
                            cmd = [
                                "ffmpeg", "-y", "-i", video_path,
                                "-ss", "00:00:01", "-vframes", "1",
                                "-vf", "scale=240:-1", thumb_path
                            ]
                            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        
                        if os.path.exists(thumb_path):
                            item.setIcon(QIcon(thumb_path))
                    
                    self.record_list.addItem(item)
                except Exception as e:
                    print(f"Error loading {f}: {e}")

    def load_recording(self, item):
        json_filename = item.text()
        json_path = os.path.join(self.config.SAVE_DIR, json_filename)
        
        self.log_list.clear()
        self.stacked_widget.setCurrentWidget(self.player_page)
        
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            video_path = data.get("local_video_path")
            
            # フォールバック: JSON内のパスが無効な場合、同階層の同名動画を探す
            if not video_path or not os.path.exists(video_path):
                base_name = os.path.splitext(json_filename)[0]
                for ext in ['.mkv', '.mp4', '.avi']:
                    fallback = os.path.join(self.config.SAVE_DIR, base_name + ext)
                    if os.path.exists(fallback):
                        video_path = fallback
                        break
            
            if video_path and os.path.exists(video_path):
                abs_path = os.path.abspath(video_path)
                self.media_player.setSource(QUrl.fromLocalFile(abs_path))
                self.media_player.play()
                self.play_btn.setText("PAUSE")
            else:
                self.media_player.setSource(QUrl())
                self.add_error_log(f"Video file not found for {json_filename}. Expected: {data.get('local_video_path')}")
                
            if "kills" in data:
                for kill in data["kills"]:
                    time_ms = kill.get("kill_time_in_match", 0)
                    killer = kill.get("killer_display_name", "Unknown")
                    victim = kill.get("victim_display_name", "Unknown")
                    
                    event_text = f"[{time_ms // 60000:02d}:{(time_ms // 1000) % 60:02d}] {killer} ⚔ {victim}"
                    list_item = QListWidgetItem(event_text)
                    list_item.setData(Qt.ItemDataRole.UserRole, time_ms)
                    self.log_list.addItem(list_item)
                    
        except Exception as e:
            self.add_error_log(f"Error loading recording data: {e}")

    def add_error_log(self, message: str):
        error_item = QListWidgetItem(message)
        error_item.setForeground(Qt.GlobalColor.red)
        self.log_list.addItem(error_item)

    def handle_media_error(self, error, error_string):
        self.add_error_log(f"Playback Error: {error_string} (Code: {error})")

    def toggle_play(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.play_btn.setText("PLAY")
        else:
            self.media_player.play()
            self.play_btn.setText("PAUSE")

    def position_changed(self, position):
        self.slider.setValue(position)

    def duration_changed(self, duration):
        self.slider.setRange(0, duration)

    def set_position(self, position):
        self.media_player.setPosition(position)
        
    def seek_to_log(self, item):
        time_ms = item.data(Qt.ItemDataRole.UserRole)
        if time_ms is not None:
            self.media_player.setPosition(time_ms)