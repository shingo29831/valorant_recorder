import os
import json
import subprocess
import re
from datetime import datetime
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QListWidget, 
                             QPushButton, QLabel, QSlider, QListWidgetItem, QStackedWidget,
                             QStyleOptionSlider, QStyle)
from PyQt6.QtCore import Qt, QUrl, QSize, pyqtSignal
from PyQt6.QtGui import QIcon, QPainter, QColor
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from core.config import Config

class TimelineOverlay(QWidget):
    seekRequested = pyqtSignal(int)

    def __init__(self, slider: QSlider, parent=None):
        super().__init__(parent)
        self.slider = slider
        self.rounds = []
        self.events = []
        self.duration = 0
        self.setFixedHeight(45)
        self.setMouseTracking(True)
        self.hover_x = -1

    def _get_slider_geometry(self):
        opt = QStyleOptionSlider()
        self.slider.initStyleOption(opt)
        
        opt.sliderPosition = opt.minimum
        min_rect = self.slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self.slider)
            
        opt.sliderPosition = opt.maximum
        max_rect = self.slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self.slider)
            
        start_x = min_rect.center().x()
        end_x = max_rect.center().x()
        
        if end_x <= start_x:
            return 10, self.width() - 20
            
        return start_x, end_x - start_x

    def set_duration(self, duration):
        self.duration = duration
        self.update()

    def set_data(self, rounds, events):
        self.rounds = rounds
        self.events = events
        self.update()

    def leaveEvent(self, event):
        self.hover_x = -1
        self.update()
        super().leaveEvent(event)
        
    def mouseMoveEvent(self, event):
        self.hover_x = event.position().x()
        self.update()
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        if self.duration <= 0:
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        height = self.height()
        start_x, draw_width = self._get_slider_geometry()
        
        round_y = height - 8
        round_h = 8
        
        painter.fillRect(start_x, round_y, draw_width, round_h, QColor("#444444"))
        
        # 1分（60000ms）ごとの目盛りを描画
        painter.setPen(QColor("#888888"))
        for ms in range(0, self.duration, 60000):
            x = start_x + (ms / self.duration) * draw_width
            painter.drawLine(int(x), round_y, int(x), round_y + round_h)
            
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FF4655"))
        for r in self.rounds:
            x1 = start_x + (r['start'] / self.duration) * draw_width
            x2 = start_x + (r['end'] / self.duration) * draw_width
            painter.drawRect(int(x1), round_y, int(max(1, x2 - x1)), round_h)
            
        for ev in self.events:
            x = start_x + (ev['time'] / self.duration) * draw_width
            
            if ev['type'] == 'kill':
                color = QColor("#00FF00")
            elif ev['type'] == 'death':
                color = QColor("#FF0000")
            elif ev['type'] == 'assist':
                color = QColor("#00A2FF")
            else:
                color = QColor("#FFFFFF")
                
            # 縦線を描画
            painter.setPen(color)
            painter.drawLine(int(x), round_y - 14, int(x), round_y)
                
            # アイコンを描画
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(int(x) - 4, round_y - 18, 8, 8)
            
        # ホバー時の時間表示
        if self.hover_x >= start_x and self.hover_x <= start_x + draw_width:
            ratio = (self.hover_x - start_x) / draw_width
            hover_ms = int(ratio * self.duration)
            
            s = hover_ms // 1000
            m = s // 60
            s = s % 60
            time_str = f"{m:02d}:{s:02d}"
            
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(int(self.hover_x) - 15, round_y - 25, time_str)

    def mousePressEvent(self, event):
        if self.duration <= 0:
            return
            
        start_x, draw_width = self._get_slider_geometry()
        x = event.position().x() - start_x
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
        
        self.timeline_overlay = TimelineOverlay(self.slider)
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
        
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setFixedWidth(100)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        controls_layout.addWidget(self.play_btn)
        controls_layout.addWidget(self.time_label)
        controls_layout.addLayout(slider_layout)
        
        layout.addWidget(self.video_widget, stretch=1)
        layout.addLayout(controls_layout)
        
        self.player_page.setLayout(layout)

    def show_list_page(self):
        self.media_player.stop()
        self.stacked_widget.setCurrentWidget(self.list_page)

    def _find_video_for_json(self, json_filename: str, json_data: dict) -> str:
        match_info = json_data.get("match_info", json_data)
        video_path = match_info.get("local_video_path") or json_data.get("local_video_path")
        
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
                    base_name = name.split("#")[0]
                    counts[base_name] = counts.get(base_name, 0) + 1
            
            for ast in k.get("assistants", []):
                name = ""
                if isinstance(ast, dict):
                    name = ast.get("assistant_display_name", "")
                elif isinstance(ast, str):
                    name = ast
                if name and name != "Unknown":
                    base_name = name.split("#")[0]
                    counts[base_name] = counts.get(base_name, 0) + 1
                    
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
                self.current_match_data = json.load(f)
                
            video_path = self._find_video_for_json(json_filename, self.current_match_data)
            
            if video_path and os.path.exists(video_path):
                abs_path = os.path.abspath(video_path)
                self.media_player.setSource(QUrl.fromLocalFile(abs_path))
                self.media_player.play()
                self.play_btn.setText("PAUSE")
            else:
                self.media_player.setSource(QUrl())
                print(f"[PlayerTab] Video file not found for {json_filename}.")
                self._update_timeline_data(0)
                
        except Exception as e:
            print(f"[PlayerTab] Error loading recording data: {e}")

    def _update_timeline_data(self, duration_ms):
        if not hasattr(self, 'current_match_data'):
            return
            
        match_info = self.current_match_data.get("match_info", self.current_match_data)
        
        offset_ms = 0
        if "local_match_start_time" in match_info and "local_match_end_time" in match_info and duration_ms > 0:
            start_time = match_info["local_match_start_time"]
            end_time = match_info["local_match_end_time"]
            video_zero_local = end_time - (duration_ms / 1000.0)
            
            game_start = match_info.get("metadata", {}).get("game_start")
            game_length = match_info.get("metadata", {}).get("game_length")
            
            if game_start and game_length:
                game_start_sec = game_start / 1000.0 if game_start > 1e11 else game_start
                game_length_sec = game_length / 1000.0 if game_length > 100000 else game_length
                
                # PCの時計が大きくズレている場合は、録画終了と試合終了を基準にした後方合わせを行う
                if abs(start_time - game_start_sec) > 300:
                    end_delay_sec = 6.0
                    offset_sec = -((duration_ms / 1000.0) - game_length_sec - end_delay_sec)
                else:
                    # 時計が合っている場合は絶対時間で計算
                    offset_sec = video_zero_local - game_start_sec
            else:
                offset_sec = video_zero_local - start_time
                
            offset_ms = int(offset_sec * 1000)
        else:
            if "video_offset_ms" in match_info:
                offset_ms = match_info["video_offset_ms"]
            else:
                video_path = match_info.get("local_video_path", "")
                basename = os.path.basename(video_path)
                date_pattern = re.compile(r"(\d{8}_\d{6})")
                vid_match = date_pattern.search(basename)
                if vid_match:
                    try:
                        vid_time = datetime.strptime(vid_match.group(1), "%Y%m%d_%H%M%S")
                        vid_timestamp = vid_time.timestamp()
                        game_start = match_info.get("metadata", {}).get("game_start")
                        if game_start:
                            offset_ms = int((vid_timestamp - game_start) * 1000)
                    except Exception:
                        pass

        events = []
        rounds = []
        
        target_puuid = None
        target_display_name = None
        
        riot_id = getattr(self.config, "RIOT_ID", "").lower()
        tag_line = getattr(self.config, "TAG_LINE", "").lower()
        
        players = match_info.get("players", {}).get("all_players", [])
        for p in players:
            p_name = p.get("name", "").lower()
            p_tag = p.get("tag", "").lower()
            if p_name == riot_id and p_tag == tag_line:
                target_puuid = p.get("puuid")
                target_display_name = p.get("name")
                break
        
        kills_data = match_info.get("kills", [])
        
        if not target_puuid and kills_data:
            target_display_name = self._guess_player_name(kills_data)
            
        for kill in kills_data:
            time_ms = int(kill.get("kill_time_in_match", 0) - offset_ms)
            if time_ms < 0:
                continue
            
            if target_puuid:
                killer_puuid = kill.get("killer_puuid")
                victim_puuid = kill.get("victim_puuid")
                
                assistants = kill.get("assistants", [])
                assistant_puuids = []
                for ast in assistants:
                    if isinstance(ast, dict):
                        assistant_puuids.append(ast.get("assistant_puuid"))
                    elif isinstance(ast, str):
                        assistant_puuids.append(ast)
                        
                if killer_puuid == target_puuid:
                    events.append({"time": time_ms, "type": "kill"})
                elif victim_puuid == target_puuid:
                    events.append({"time": time_ms, "type": "death"})
                elif target_puuid in assistant_puuids:
                    events.append({"time": time_ms, "type": "assist"})
            else:
                killer = kill.get("killer_display_name", "Unknown")
                victim = kill.get("victim_display_name", "Unknown")
                
                assistants = kill.get("assistants", [])
                assistant_names = []
                for ast in assistants:
                    if isinstance(ast, dict):
                        assistant_names.append(ast.get("assistant_display_name", ""))
                    elif isinstance(ast, str):
                        assistant_names.append(ast)
                        
                if target_display_name and target_display_name in killer:
                    events.append({"time": time_ms, "type": "kill"})
                elif target_display_name and target_display_name in victim:
                    events.append({"time": time_ms, "type": "death"})
                elif target_display_name and any(target_display_name in ast for ast in assistant_names):
                    events.append({"time": time_ms, "type": "assist"})
                elif not target_display_name:
                    events.append({"time": time_ms, "type": "kill"})
                
        for r in match_info.get("rounds", []):
            start = int(r.get("start_time_in_match", 0) - offset_ms)
            end = int(r.get("end_time_in_match", 0) - offset_ms)
            if end > 0:
                rounds.append({"start": max(0, start), "end": end})
            
        self.timeline_overlay.set_data(rounds, events)

    def handle_media_error(self, error, error_string):
        print(f"[PlayerTab] Playback Error: {error_string} (Code: {error})")

    def toggle_play(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.play_btn.setText("PLAY")
        else:
            self.media_player.play()
            self.play_btn.setText("PAUSE")

    def format_time(self, ms):
        s = ms // 1000
        m = s // 60
        s = s % 60
        return f"{m:02d}:{s:02d}"

    def position_changed(self, position):
        self.slider.setValue(position)
        duration = self.media_player.duration()
        self.time_label.setText(f"{self.format_time(position)} / {self.format_time(duration)}")

    def duration_changed(self, duration):
        self.slider.setRange(0, duration)
        self.timeline_overlay.set_duration(duration)
        position = self.media_player.position()
        self.time_label.setText(f"{self.format_time(position)} / {self.format_time(duration)}")
        
        if duration > 0:
            self._update_timeline_data(duration)

    def set_position(self, position):
        self.media_player.setPosition(position)