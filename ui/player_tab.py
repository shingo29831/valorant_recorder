import os
import json
import subprocess
import re
from datetime import datetime
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, 
                             QPushButton, QLabel, QStackedWidget,
                             QSizePolicy, QScrollArea, QInputDialog)
from PyQt6.QtCore import Qt, QUrl, QSize, pyqtSignal, QByteArray
from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtSvg import QSvgRenderer
from core.config import Config
from ui.player_components import (ClickableVideoWidget, FlowLayout, RecordItemWidget, 
                                  VolumeWidget, TimelineOverlay, PlayerContainer)

SETTINGS_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M19.14,12.94c0.04-0.3,0.06-0.61,0.06-0.94c0-0.32-0.02-0.64-0.06-0.94l2.03-1.58c0.18-0.14,0.23-0.41,0.12-0.61 l-1.92-3.32c-0.12-0.22-0.37-0.29-0.59-0.22l-2.39,0.96c-0.5-0.38-1.03-0.7-1.62-0.94L14.4,2.81c-0.04-0.24-0.24-0.41-0.48-0.41 h-3.84c-0.24,0-0.43,0.17-0.47,0.41L9.25,5.35C8.66,5.59,8.12,5.92,7.63,6.29L5.24,5.33c-0.22-0.08-0.47,0-0.59,0.22L2.73,8.87 C2.62,9.08,2.66,9.34,2.86,9.48l2.03,1.58C4.84,11.36,4.8,11.69,4.8,12s0.02,0.64,0.06,0.94l-2.03,1.58 c-0.18,0.14-0.23,0.41-0.12,0.61l1.92,3.32c0.12,0.22,0.37,0.29,0.59,0.22l2.39-0.96c0.5,0.38,1.03,0.7,1.62,0.94l0.36,2.54 c0.05,0.24,0.24,0.41,0.48,0.41h3.84c0.24,0,0.43-0.17,0.47-0.41l0.36-2.54c0.59-0.24,1.13-0.56,1.62-0.94l2.39,0.96 c0.22,0.08,0.47,0,0.59-0.22l1.92-3.32c0.12-0.22,0.07-0.49-0.12-0.61L19.14,12.94z M12,15.6c-1.98,0-3.6-1.62-3.6-3.6 s1.62-3.6,3.6-3.6s3.6,1.62,3.6,3.6S13.98,15.6,12,15.6z"/>
</svg>"""

BACK_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M20,11V13H8L13.5,18.5L12.08,19.92L4.16,12L12.08,4.08L13.5,5.5L8,11H20Z" />
</svg>"""

class PlayerTab(QWidget):
    settingsRequested = pyqtSignal()

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
        
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addStretch()
        
        settings_btn = QPushButton(" Settings")
        settings_btn.setFixedSize(100, 30)
        settings_btn.setStyleSheet("border-radius: 15px; background-color: #333333; color: white; font-weight: bold; text-align: center;")
        
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer = QSvgRenderer(QByteArray(SETTINGS_SVG))
        renderer.render(painter)
        painter.end()
        
        settings_btn.setIcon(QIcon(pixmap))
        settings_btn.setIconSize(QSize(16, 16))
        settings_btn.clicked.connect(self.settingsRequested.emit)
        
        top_layout.addWidget(settings_btn)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background-color: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.scroll_area.setWidget(self.scroll_content)
        
        layout.addLayout(top_layout)
        layout.addWidget(self.scroll_area)
        self.list_page.setLayout(layout)

    def _clear_layout(self, layout):
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
                elif item.layout():
                    self._clear_layout(item.layout())
            layout.deleteLater()

    def rename_record(self, json_filename, current_name):
        new_name, ok = QInputDialog.getText(self, "Rename", "Enter new name:", text=current_name)
        if ok and new_name and new_name != current_name:
            json_path = os.path.join(self.config.SAVE_DIR, json_filename)
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                data["custom_name"] = new_name
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                self.refresh_list()
            except Exception as e:
                print(f"[PlayerTab] Error saving custom name: {e}")

    def setup_player_page(self):
        self.player_page = QWidget()
        page_layout = QVBoxLayout()
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
        back_btn.clicked.connect(self.show_list_page)
        
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
        self.volume_widget.volumeChanged.connect(self.audio_output.setVolume)
        
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setFixedWidth(100)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        controls_layout.addWidget(self.volume_widget)
        controls_layout.addWidget(self.time_label)
        controls_layout.addWidget(self.timeline_overlay)
        
        self.player_container = PlayerContainer(self.video_widget)
        
        right_container = QWidget()
        right_container.setFixedWidth(50)
        
        top_layout.addWidget(left_container)
        top_layout.addWidget(self.player_container, stretch=1)
        top_layout.addWidget(right_container)
        
        page_layout.addLayout(top_layout, stretch=1)
        page_layout.addWidget(controls_widget)
        
        self.player_page.setLayout(page_layout)

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

    def _get_agent_name(self, match_info: dict, kills_data: list) -> str:
        riot_id = getattr(self.config, "RIOT_ID", "").lower()
        tag_line = getattr(self.config, "TAG_LINE", "").lower()
        
        players = match_info.get("players", {}).get("all_players", [])
        
        for p in players:
            p_name = p.get("name", "").lower()
            p_tag = p.get("tag", "").lower()
            if p_name == riot_id and p_tag == tag_line:
                return p.get("character", "Unknown Agent")
                
        target_display_name = self._guess_player_name(kills_data)
        if target_display_name:
            for p in players:
                if p.get("name") == target_display_name:
                    return p.get("character", "Unknown Agent")
                    
        return "Unknown Agent"

    def _get_match_result(self, match_info: dict, kills_data: list) -> str:
        riot_id = getattr(self.config, "RIOT_ID", "").lower()
        tag_line = getattr(self.config, "TAG_LINE", "").lower()
        
        target_team = None
        players = match_info.get("players", {}).get("all_players", [])
        
        for p in players:
            p_name = p.get("name", "").lower()
            p_tag = p.get("tag", "").lower()
            if p_name == riot_id and p_tag == tag_line:
                target_team = p.get("team", "").lower()
                break
                
        if not target_team:
            target_display_name = self._guess_player_name(kills_data)
            if target_display_name:
                for p in players:
                    if p.get("name") == target_display_name:
                        target_team = p.get("team", "").lower()
                        break
                        
        if not target_team:
            return "unknown"
            
        teams = match_info.get("teams", {})
        team_info = teams.get(target_team)
        if team_info:
            has_won = team_info.get("has_won")
            if has_won:
                return "win"
            else:
                other_team = "blue" if target_team == "red" else "red"
                other_team_info = teams.get(other_team, {})
                if not has_won and not other_team_info.get("has_won"):
                    return "draw"
                return "loss"
                
        return "unknown"

    def refresh_list(self):
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())
                
        if not os.path.exists(self.config.SAVE_DIR):
            return
            
        records_by_date = {}
            
        for f in sorted(os.listdir(self.config.SAVE_DIR), reverse=True):
            if f.endswith(".json"):
                json_path = os.path.join(self.config.SAVE_DIR, f)
                try:
                    with open(json_path, 'r', encoding='utf-8') as jf:
                        data = json.load(jf)
                    
                    match_info = data.get("match_info", data)
                    custom_name = data.get("custom_name")
                    
                    game_start = match_info.get("metadata", {}).get("game_start")
                    if game_start:
                        dt = datetime.fromtimestamp(game_start)
                    else:
                        date_match = re.search(r"(\d{8}_\d{6})", f)
                        if date_match:
                            try:
                                dt = datetime.strptime(date_match.group(1), "%Y%m%d_%H%M%S")
                            except ValueError:
                                dt = datetime.now()
                        else:
                            dt = datetime.now()
                            
                    date_key = dt.strftime('%Y-%m-%d')
                    time_str = dt.strftime('%H:%M')
                    
                    kills_data = match_info.get("kills", [])
                    
                    if custom_name:
                        display_name = custom_name
                    else:
                        mode = match_info.get("metadata", {}).get("mode", "Unknown")
                        map_name = match_info.get("metadata", {}).get("map", "Unknown")
                        agent_name = self._get_agent_name(match_info, kills_data)
                        
                        display_name = f"{mode} - {map_name} - {agent_name} - {date_key} {time_str}"
                    
                    result = self._get_match_result(match_info, kills_data)
                    video_path = self._find_video_for_json(f, data)
                    
                    thumb_path = ""
                    if video_path and os.path.exists(video_path):
                        thumb_path = os.path.join(self.config.SAVE_DIR, f.replace('.json', '.jpg'))
                        if not os.path.exists(thumb_path):
                            cmd = [
                                "ffmpeg", "-y", "-i", video_path,
                                "-ss", "00:00:01", "-vframes", "1",
                                "-vf", "scale=240:-1", thumb_path
                            ]
                            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            
                    if date_key not in records_by_date:
                        records_by_date[date_key] = []
                        
                    records_by_date[date_key].append({
                        'filename': f,
                        'display_name': display_name,
                        'thumb_path': thumb_path if os.path.exists(thumb_path) else "",
                        'result': result
                    })
                    
                except Exception as e:
                    print(f"[PlayerTab] Error loading {f}: {e}")
                    
        for date_key in sorted(records_by_date.keys(), reverse=True):
            date_label = QLabel(date_key)
            date_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #FF4655; margin-top: 15px; margin-bottom: 5px;")
            self.scroll_layout.addWidget(date_label)
            
            flow_widget = QWidget()
            flow_layout = FlowLayout(flow_widget)
            
            for rec in records_by_date[date_key]:
                item_widget = RecordItemWidget(rec['filename'], rec['display_name'], rec['thumb_path'], rec['result'])
                item_widget.doubleClicked.connect(self.load_recording_by_filename)
                item_widget.renameRequested.connect(self.rename_record)
                flow_layout.addWidget(item_widget)
                
            self.scroll_layout.addWidget(flow_widget)
            
        self.scroll_layout.addStretch()

    def load_recording_by_filename(self, json_filename):
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