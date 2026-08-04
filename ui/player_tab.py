import os
import json
import subprocess
import re
from datetime import datetime
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QListWidget, 
                             QPushButton, QLabel, QSlider, QListWidgetItem, QStackedWidget)
from PyQt6.QtCore import Qt, QUrl, QSize, pyqtSignal
from PyQt6.QtGui import QIcon, QPainter, QColor
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from core.config import Config

class TimelineOverlay(QWidget):
    seekRequested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rounds = []
        self.events = []
        self.duration = 0
        self.setFixedHeight(30)

    def set_duration(self, duration):
        self.duration = duration
        self.update()

    def set_data(self, rounds, events):
        self.rounds = rounds
        self.events = events
        self.update()

    def paintEvent(self, event):
        if self.duration <= 0:
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        width = self.width()
        height = self.height()
        
        # QSliderのデフォルトの左右マージンに合わせる
        margin = 10
        draw_width = width - margin * 2
        if draw_width <= 0:
            return
            
        # ラウンドの描画 (下部 8px)
        round_y = height - 8
        round_h = 8
        
        # 背景（準備フェーズ / ラウンド外）
        painter.fillRect(margin, round_y, draw_width, round_h, QColor("#444444"))
        
        # ラウンド中（Action Phase）
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FF4655"))
        for r in self.rounds:
            start_x = margin + (r['start'] / self.duration) * draw_width
            end_x = margin + (r['end'] / self.duration) * draw_width
            painter.drawRect(int(start_x), round_y, int(max(1, end_x - start_x)), round_h)
            
        # イベントアイコンの描画
        for ev in self.events:
            x = margin + (ev['time'] / self.duration) * draw_width
            
            if ev['type'] == 'kill':
                color = QColor("#00FF00") # 緑
            elif ev['type'] == 'death':
                color = QColor("#FF0000") # 赤
            elif ev['type'] == 'assist':
                color = QColor("#00A2FF") # 青
            else:
                color = QColor("#FFFFFF")
                
            painter.setBrush(color)
            painter.drawEllipse(int(x) - 4, round_y - 14, 8, 8)

    def mousePressEvent(self, event):
        if self.duration <= 0:
            return
        margin = 10
        draw_width = self.width() - margin * 2
        x = event.position().x() - margin
        x = max(0, min(x, draw_width))
        
        pos_ms = int((x / draw_width) * self.duration)
        self.seekRequested.emit(pos_ms)


class PlayerTab(QWidget):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        
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
        
        self.timeline_overlay = TimelineOverlay()
        self.timeline_overlay.seekRequested.connect(self.set_position)
        
        slider_layout = QVBoxLayout()
        slider_layout.setSpacing(0)
        slider_layout.setContentsMargins(0, 0, 0, 0)
        slider_layout.addWidget(self.timeline_overlay)
        slider_layout.addWidget(self.slider)
        
        controls_layout = QHBoxLayout()
        self.play_btn = QPushButton("PLAY")
        self.play_btn.setFixedWidth(100)
        self.play_btn.clicked.connect(self.toggle_play)
        controls_layout.addWidget(self.play_btn)
        controls_layout.addLayout(slider_layout)
        
        layout.addWidget(self.video_widget, stretch=1)
        layout.addLayout(controls_layout)
        
        self.player_page.setLayout(layout)

    def show_list_page(self):
        self.media_player.stop()
        self.stacked_widget.setCurrentWidget(self.list_page)

    def _find_video_for_json(self, json_filename: str, json_data: dict) -> str:
        video_path = json_data.get("local_video_path")
        
        if video_path:
            video_path = video_path.replace("\\", "/")
            if video_path.startswith("./"):
                abs_path = os.path.abspath(video_path)
                if os.path.exists(abs_path):
                    return abs_path
            elif os.path.exists(video_path):
                return os.path.abspath(video_path)
                
            basename = os.path.basename(video_path)
            fallback_path = os.path.join(self.config.SAVE_DIR, basename)
            if os.path.exists(fallback_path):
                return fallback_path

        date_pattern = re.compile(r"(\d{8}_\d{6})")
        json_match = date_pattern.search(json_filename)
        
        if not json_match:
            return ""
            
        try:
            json_time = datetime.strptime(json_match.group(1), "%Y%m%d_%H%M%S")
        except ValueError:
            return ""

        best_video = ""
        min_diff = float('inf')

        for f in os.listdir(self.config.SAVE_DIR):
            if f.endswith(('.mp4', '.mkv', '.avi')):
                vid_match = date_pattern.search(f)
                if vid_match:
                    try:
                        vid_time = datetime.strptime(vid_match.group(1), "%Y%m%d_%H%M%S")
                        diff = abs((json_time - vid_time).total_seconds())
                        if diff < min_diff and diff < 7200:
                            min_diff = diff
                            best_video = os.path.join(self.config.SAVE_DIR, f)
                    except ValueError:
                        continue
                        
        return best_video

    def _guess_player_name(self, kills: list) -> str:
        counts = {}
        for k in kills:
            for key in ["killer_display_name", "victim_display_name"]:
                name = k.get(key)
                if name and name != "Unknown":
                    counts[name] = counts.get(name, 0) + 1
            for name in k.get("assistants", []):
                if name and name != "Unknown":
                    counts[name] = counts.get(name, 0) + 1
        if counts:
            return max(counts, key=counts.get)
        return ""

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
                    
                    video_path = self._find_video_for_json(f, data)
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
                    print(f"[PlayerTab] Error loading {f}: {e}")

    def load_recording(self, item):
        json_filename = item.text()
        json_path = os.path.join(self.config.SAVE_DIR, json_filename)
        
        self.stacked_widget.setCurrentWidget(self.player_page)
        
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            video_path = self._find_video_for_json(json_filename, data)
            
            if video_path and os.path.exists(video_path):
                abs_path = os.path.abspath(video_path)
                self.media_player.setSource(QUrl.fromLocalFile(abs_path))
                self.media_player.play()
                self.play_btn.setText("PAUSE")
            else:
                self.media_player.setSource(QUrl())
                print(f"[PlayerTab] Video file not found for {json_filename}. Expected: {data.get('local_video_path')}")
                
            events = []
            rounds = []
            
            player_name = data.get("player_name", getattr(self.config, "PLAYER_NAME", ""))
            kills_data = data.get("kills", [])
            
            if not player_name and kills_data:
                player_name = self._guess_player_name(kills_data)
                
            for kill in kills_data:
                time_ms = kill.get("kill_time_in_match", 0)
                killer = kill.get("killer_display_name", "Unknown")
                victim = kill.get("victim_display_name", "Unknown")
                assistants = kill.get("assistants", [])
                
                if killer == player_name:
                    events.append({"time": time_ms, "type": "kill"})
                elif victim == player_name:
                    events.append({"time": time_ms, "type": "death"})
                elif player_name in assistants:
                    events.append({"time": time_ms, "type": "assist"})
                elif not player_name:
                    events.append({"time": time_ms, "type": "kill"})
                    
            for r in data.get("rounds", []):
                start = r.get("start_time_in_match", 0)
                end = r.get("end_time_in_match", 0)
                rounds.append({"start": start, "end": end})
                
            self.timeline_overlay.set_data(rounds, events)
                    
        except Exception as e:
            print(f"[PlayerTab] Error loading recording data: {e}")

    def handle_media_error(self, error, error_string):
        print(f"[PlayerTab] Playback Error: {error_string} (Code: {error})")

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
        self.timeline_overlay.set_duration(duration)

    def set_position(self, position):
        self.media_player.setPosition(position)