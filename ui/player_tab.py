import os
import json
import subprocess
import re
from datetime import datetime
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QListWidget, 
                             QPushButton, QLabel, QListWidgetItem, QStackedWidget,
                             QSizePolicy, QSlider)
from PyQt6.QtCore import Qt, QUrl, QSize, pyqtSignal, QTimer
from PyQt6.QtGui import QIcon, QPainter, QColor, QPen
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from core.config import Config

class VolumePopup(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setFixedSize(40, 120)
        self.setStyleSheet("""
            VolumePopup { background-color: #222222; border: 1px solid #444444; border-radius: 4px; }
            QSlider { background: transparent; }
            QSlider::groove:vertical { background: #444444; width: 4px; border-radius: 2px; }
            QSlider::handle:vertical { background: #FFFFFF; height: 12px; margin: 0 -4px; border-radius: 6px; }
            QSlider::sub-page:vertical { background: #FF4655; width: 4px; border-radius: 2px; }
            QSlider::add-page:vertical { background: #444444; width: 4px; border-radius: 2px; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        self.slider = QSlider(Qt.Orientation.Vertical)
        self.slider.setRange(0, 100)
        self.slider.setValue(100)
        layout.addWidget(self.slider, alignment=Qt.AlignmentFlag.AlignHCenter)

    def leaveEvent(self, event):
        QTimer.singleShot(100, self._check_hide)
        super().leaveEvent(event)
        
    def _check_hide(self):
        cursor_pos = self.cursor().pos()
        parent_widget = self.parent()
        if parent_widget:
            if not self.geometry().contains(cursor_pos) and not parent_widget.rect().contains(parent_widget.mapFromGlobal(cursor_pos)):
                self.hide()
        else:
            if not self.geometry().contains(cursor_pos):
                self.hide()

class VolumeWidget(QWidget):
    volumeChanged = pyqtSignal(float)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 30)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.icon_label = QLabel("🔊")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("font-size: 16px; color: white;")
        layout.addWidget(self.icon_label)
        
        self.popup = VolumePopup(self)
        self.popup.slider.valueChanged.connect(self._on_value_changed)
        
    def _on_value_changed(self, val):
        self.volumeChanged.emit(val / 100.0)
        if val == 0:
            self.icon_label.setText("🔇")
        elif val < 50:
            self.icon_label.setText("🔉")
        else:
            self.icon_label.setText("🔊")
            
    def enterEvent(self, event):
        pos = self.mapToGlobal(self.rect().topLeft())
        self.popup.move(pos.x() - 5, pos.y() - self.popup.height() + 5)
        self.popup.show()
        super().enterEvent(event)
        
    def leaveEvent(self, event):
        QTimer.singleShot(100, self._check_hide)
        super().leaveEvent(event)

    def _check_hide(self):
        cursor_pos = self.popup.cursor().pos()
        if not self.popup.geometry().contains(cursor_pos) and not self.rect().contains(self.mapFromGlobal(cursor_pos)):
            self.popup.hide()

class TimelineOverlay(QWidget):
    seekRequested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rounds = []
        self.events = []
        self.duration = 0
        self.position = 0
        self.setFixedHeight(45)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.hover_x = -1
        self.is_dragging = False

    def set_duration(self, duration):
        self.duration = duration
        self.update()

    def set_position(self, position):
        self.position = position
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
        if self.is_dragging and self.duration > 0:
            x = max(0, min(self.hover_x, self.width()))
            pos_ms = int((x / self.width()) * self.duration)
            self.seekRequested.emit(pos_ms)
        self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if self.duration <= 0 or event.button() != Qt.MouseButton.LeftButton:
            return
        self.is_dragging = True
        x = event.position().x()
        x = max(0, min(x, self.width()))
        pos_ms = int((x / self.width()) * self.duration)
        self.seekRequested.emit(pos_ms)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = False
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        if self.duration <= 0:
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        width = self.width()
        height = self.height()
        
        round_y = height - 12
        round_h = 12
        
        painter.fillRect(0, round_y, width, round_h, QColor("#222222"))
        
        painter.setPen(Qt.PenStyle.NoPen)
        for r in self.rounds:
            x1 = (r['start'] / self.duration) * width
            x2 = (r['end'] / self.duration) * width
            
            phase = r.get('phase')
            if phase == 'InProgress':
                painter.setBrush(QColor("#666666"))
            elif phase == 'PreRound':
                painter.setBrush(QColor("#444444"))
            elif phase == 'PostRound':
                painter.setBrush(QColor("#333333"))
            else:
                painter.setBrush(QColor("#555555"))
                
            painter.drawRect(int(x1), round_y, int(max(1, x2 - x1)), round_h)
            
        progress_w = (self.position / self.duration) * width
        painter.setBrush(QColor(255, 70, 85, 150))
        painter.drawRect(0, round_y, int(progress_w), round_h)
        
        painter.setPen(QPen(QColor("#FF4655"), 2))
        painter.drawLine(int(progress_w), round_y - 2, int(progress_w), round_y + round_h + 2)
            
        painter.setPen(QColor("#888888"))
        for ms in range(0, self.duration, 60000):
            x = (ms / self.duration) * width
            painter.drawLine(int(x), round_y, int(x), round_y + round_h)
            
        for ev in self.events:
            x = (ev['time'] / self.duration) * width
            
            if ev['type'] == 'kill':
                color = QColor("#00FF00")
            elif ev['type'] == 'death':
                color = QColor("#FF0000")
            elif ev['type'] == 'assist':
                color = QColor("#00A2FF")
            else:
                color = QColor("#FFFFFF")
                
            painter.setPen(color)
            painter.drawLine(int(x), round_y - 14, int(x), round_y)
                
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(int(x) - 4, round_y - 18, 8, 8)
            
        if self.hover_x >= 0 and self.hover_x <= width:
            ratio = self.hover_x / width
            hover_ms = int(ratio * self.duration)
            
            s = hover_ms // 1000
            m = s // 60
            s = s % 60
            time_str = f"{m:02d}:{s:02d}"
            
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(int(self.hover_x) - 15, round_y - 25, time_str)

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
        
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        
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
        
        self.timeline_overlay = TimelineOverlay()
        self.timeline_overlay.seekRequested.connect(self.set_position)
        self.media_player.positionChanged.connect(self.position_changed)
        self.media_player.durationChanged.connect(self.duration_changed)
        self.media_player.errorOccurred.connect(self.handle_media_error)
        
        controls_layout = QHBoxLayout()
        self.play_btn = QPushButton("PLAY")
        self.play_btn.setFixedWidth(100)
        self.play_btn.clicked.connect(self.toggle_play)
        
        self.volume_widget = VolumeWidget()
        self.volume_widget.volumeChanged.connect(self.audio_output.setVolume)
        
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setFixedWidth(100)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        controls_layout.addWidget(self.play_btn)
        controls_layout.addWidget(self.volume_widget)
        controls_layout.addWidget(self.time_label)
        controls_layout.addWidget(self.timeline_overlay)
        
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
        local_round_events = match_info.get("local_round_events", [])
        kills_data = match_info.get("kills", [])
        
        api_first_start = 0
        if kills_data:
            first_kill = kills_data[0]
            k_match = first_kill.get("kill_time_in_match", 0)
            k_round = first_kill.get("kill_time_in_round", 0)
            if k_match > 0 and k_round > 0:
                api_first_start = k_match - k_round

        if local_round_events and api_first_start > 0:
            first_local_preround = next((ev for ev in local_round_events if ev["phase"] == "PreRound"), None)
            if first_local_preround:
                offset_ms = api_first_start - first_local_preround["time_ms"]
            else:
                first_local_inprogress = next((ev for ev in local_round_events if ev["phase"] == "InProgress"), None)
                if first_local_inprogress:
                    offset_ms = api_first_start - first_local_inprogress["time_ms"]
        elif "local_match_start_time" in match_info and "local_match_end_time" in match_info and duration_ms > 0:
            game_length = match_info.get("metadata", {}).get("game_length")
            if game_length:
                game_length_sec = game_length / 1000.0 if game_length > 100000 else game_length
                end_delay_sec = 6.5
                offset_sec = game_length_sec + end_delay_sec - (duration_ms / 1000.0)
                offset_ms = int(offset_sec * 1000)
            else:
                start_time = match_info["local_match_start_time"]
                end_time = match_info["local_match_end_time"]
                video_zero_local = end_time - (duration_ms / 1000.0)
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
                
        if local_round_events:
            for i in range(len(local_round_events)):
                ev = local_round_events[i]
                phase = ev["phase"]
                start_time = ev["time_ms"]
                end_time = duration_ms
                if i + 1 < len(local_round_events):
                    end_time = local_round_events[i+1]["time_ms"]
                    
                if phase in ["PreRound", "InProgress", "PostRound"]:
                    rounds.append({"start": start_time, "end": end_time, "phase": phase})
        else:
            if kills_data:
                round_starts = []
                for k in kills_data:
                    k_match = k.get("kill_time_in_match", 0)
                    k_round = k.get("kill_time_in_round", 0)
                    if k_match > 0 and k_round > 0:
                        r_start = k_match - k_round
                        if not round_starts or abs(round_starts[-1] - r_start) > 5000:
                            round_starts.append(r_start)
                
                for i, r_start in enumerate(round_starts):
                    start = int(r_start - offset_ms)
                    if i + 1 < len(round_starts):
                        end = int(round_starts[i+1] - offset_ms)
                    else:
                        game_length = match_info.get("metadata", {}).get("game_length", 0)
                        end = int(game_length - offset_ms) if game_length > 0 else duration_ms
                    
                    if end > start:
                        rounds.append({"start": max(0, start), "end": end, "phase": "InProgress"})
            
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
        self.timeline_overlay.set_position(position)
        duration = self.media_player.duration()
        self.time_label.setText(f"{self.format_time(position)} / {self.format_time(duration)}")

    def duration_changed(self, duration):
        self.timeline_overlay.set_duration(duration)
        position = self.media_player.position()
        self.time_label.setText(f"{self.format_time(position)} / {self.format_time(duration)}")
        
        if duration > 0:
            self._update_timeline_data(duration)

    def set_position(self, position):
        self.media_player.setPosition(position)