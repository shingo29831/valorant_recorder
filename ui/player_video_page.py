import os
import json
import re
from datetime import datetime
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
from PyQt6.QtCore import Qt, QUrl, QSize, pyqtSignal, QByteArray
from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtSvg import QSvgRenderer
from core.config import Config
from ui.player_components import ClickableVideoWidget, VolumeWidget, MicVolumeWidget, TimelineOverlay, PlayerContainer
from ui.player_utils import find_video_for_json, guess_player_name
from ui.event_toggle_widget import EventToggleWidget

BACK_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M20,11V13H8L13.5,18.5L12.08,19.92L4.16,12L12.08,4.08L13.5,5.5L8,11H20Z" />
</svg>"""

class PlayerVideoPage(QWidget):
    backRequested = pyqtSignal()

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.current_match_data = None
        
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(20, 20, 20, 20)
        page_layout.setSpacing(10)
        
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)
        
        left_container = QWidget()
        left_container.setFixedWidth(50)
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        back_btn = QPushButton()
        back_btn.setFixedSize(40, 40)
        back_btn.setStyleSheet("border-radius: 20px; background-color: #333333;")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer = QSvgRenderer(QByteArray(BACK_SVG))
        renderer.render(painter)
        painter.end()
        
        back_btn.setIcon(QIcon(pixmap))
        back_btn.setIconSize(QSize(24, 24))
        back_btn.clicked.connect(self.request_back)
        
        left_layout.addWidget(back_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        left_layout.addStretch()
        
        self.video_widget = ClickableVideoWidget()
        self.video_widget.setStyleSheet("background-color: #000000;")
        self.video_widget.clicked.connect(self.toggle_play)
        
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        
        self.timeline_overlay = TimelineOverlay()
        self.timeline_overlay.seekRequested.connect(self.set_position)
        self.media_player.positionChanged.connect(self.position_changed)
        self.media_player.durationChanged.connect(self.duration_changed)
        self.media_player.errorOccurred.connect(self.handle_media_error)
        
        controls_widget = QWidget()
        controls_layout = QHBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        
        self.volume_widget = VolumeWidget()
        self.volume_widget.volumeChanged.connect(self.on_sys_volume_changed)
        self.current_sys_volume = 1.0
        
        self.mic_volume_widget = MicVolumeWidget()
        self.mic_volume_widget.volumeChanged.connect(self.on_mic_volume_changed)
        self.current_mic_volume = 1.0
        
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setFixedWidth(100)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        controls_layout.addWidget(self.volume_widget)
        controls_layout.addWidget(self.mic_volume_widget)
        controls_layout.addWidget(self.time_label)
        controls_layout.addWidget(self.timeline_overlay)
        
        self.player_container = PlayerContainer(self.video_widget)
        
        self.event_toggle_widget = EventToggleWidget()
        self.event_toggle_widget.filterChanged.connect(self.timeline_overlay.set_filters)
        
        top_layout.addWidget(left_container)
        top_layout.addWidget(self.player_container, stretch=1)
        top_layout.addWidget(self.event_toggle_widget)
        
        page_layout.addLayout(top_layout, stretch=1)
        page_layout.addWidget(controls_widget)

    def request_back(self):
        self.media_player.stop()
        self.media_player.setSource(QUrl())
        self.backRequested.emit()

    def cleanup_media(self):
        # メモリ解放のため、再生を停止しソースをクリアする
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        self.media_player.setSource(QUrl())

    def restore_media(self):
        # 再表示時に元の動画を再ロードする
        if hasattr(self, 'current_json_filename') and self.current_json_filename:
            self.load_recording(self.current_json_filename)

    def on_sys_volume_changed(self, volume):
        vol_float = float(volume)
        if vol_float > 1.0:
            vol_float /= 100.0
        self.current_sys_volume = vol_float
        self.audio_output.setVolume(vol_float)
        self._save_volume_settings()

    def on_mic_volume_changed(self, volume):
        vol_float = float(volume)
        if vol_float > 1.0:
            vol_float /= 100.0
        self.current_mic_volume = vol_float
        self._save_volume_settings()

    def _save_volume_settings(self):
        if hasattr(self, 'current_json_filename') and self.current_match_data:
            json_path = os.path.join(self.config.SAVE_DIR, self.current_json_filename)
            self.current_match_data["mic_volume"] = self.current_mic_volume
            self.current_match_data["sys_volume"] = self.current_sys_volume
            try:
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(self.current_match_data, f, ensure_ascii=False, indent=4)
            except Exception as e:
                print(f"[PlayerVideoPage] Failed to save volume settings to JSON: {e}")

    def load_recording(self, json_filename):
        self.current_json_filename = json_filename
        json_path = os.path.join(self.config.SAVE_DIR, json_filename)
        
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                self.current_match_data = json.load(f)
                
            saved_mic_volume = self.current_match_data.get("mic_volume", 1.0)
            self.current_mic_volume = saved_mic_volume
            self.mic_volume_widget.set_volume(saved_mic_volume)
            
            saved_sys_volume = self.current_match_data.get("sys_volume", 1.0)
            self.current_sys_volume = saved_sys_volume
            self.audio_output.setVolume(saved_sys_volume)
                
            video_path = find_video_for_json(self.config.SAVE_DIR, json_filename, self.current_match_data)
            
            if video_path and os.path.exists(video_path):
                abs_path = os.path.abspath(video_path)
                self.media_player.setSource(QUrl.fromLocalFile(abs_path))
                self.media_player.play()
            else:
                self.media_player.setSource(QUrl())
                print(f"[PlayerVideoPage] Video file not found for {json_filename}.")
                self._update_timeline_data(0)
                
        except Exception as e:
            print(f"[PlayerVideoPage] Error loading recording data: {e}")

    def _update_timeline_data(self, duration_ms):
        if not self.current_match_data:
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
            target_display_name = guess_player_name(kills_data)
            
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
        print(f"[PlayerVideoPage] Playback Error: {error_string} (Code: {error})")

    def toggle_play(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

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