import os
import json
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QPushButton, QLabel, QSlider, QListWidgetItem
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from core.config import Config

class PlayerTab(QWidget):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.layout = QHBoxLayout()
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(20)
        
        # Left Panel: Recording List
        left_layout = QVBoxLayout()
        title_label = QLabel("MATCH RECORDINGS")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #FF4655;")
        left_layout.addWidget(title_label)
        
        self.record_list = QListWidget()
        self.record_list.setFixedWidth(280)
        self.record_list.itemClicked.connect(self.load_recording)
        left_layout.addWidget(self.record_list)
        
        refresh_btn = QPushButton("REFRESH LIST")
        refresh_btn.clicked.connect(self.refresh_list)
        left_layout.addWidget(refresh_btn)
        
        # Right Panel: Video Player and Events
        right_layout = QVBoxLayout()
        
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
        
        controls_layout = QHBoxLayout()
        self.play_btn = QPushButton("PLAY")
        self.play_btn.setFixedWidth(100)
        self.play_btn.clicked.connect(self.toggle_play)
        controls_layout.addWidget(self.play_btn)
        controls_layout.addWidget(self.slider)
        
        events_label = QLabel("MATCH EVENTS (Click to seek)")
        events_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        
        self.log_list = QListWidget()
        self.log_list.setFixedHeight(180)
        self.log_list.itemClicked.connect(self.seek_to_log)
        
        right_layout.addWidget(self.video_widget, stretch=1)
        right_layout.addLayout(controls_layout)
        right_layout.addWidget(events_label)
        right_layout.addWidget(self.log_list)
        
        self.layout.addLayout(left_layout)
        self.layout.addLayout(right_layout)
        self.setLayout(self.layout)
        
        self.refresh_list()

    def refresh_list(self):
        self.record_list.clear()
        if not os.path.exists(self.config.SAVE_DIR):
            return
            
        for f in sorted(os.listdir(self.config.SAVE_DIR), reverse=True):
            if f.endswith(".json"):
                self.record_list.addItem(f)

    def load_recording(self, item):
        json_path = os.path.join(self.config.SAVE_DIR, item.text())
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            video_path = data.get("local_video_path")
            if video_path and os.path.exists(video_path):
                abs_path = os.path.abspath(video_path)
                self.media_player.setSource(QUrl.fromLocalFile(abs_path))
                self.play_btn.setText("PLAY")
            else:
                self.media_player.setSource(QUrl())
                
            self.log_list.clear()
            
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
            print(f"Error loading recording: {e}")

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