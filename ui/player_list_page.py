import os
import json
import subprocess
import re
from datetime import datetime
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QScrollArea, QLabel, QInputDialog
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QByteArray
from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer
from core.config import Config
from ui.player_components import FlowLayout, RecordItemWidget
from ui.player_utils import find_video_for_json, get_agent_name, get_match_result

SETTINGS_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M19.14,12.94c0.04-0.3,0.06-0.61,0.06-0.94c0-0.32-0.02-0.64-0.06-0.94l2.03-1.58c0.18-0.14,0.23-0.41,0.12-0.61 l-1.92-3.32c-0.12-0.22-0.37-0.29-0.59-0.22l-2.39,0.96c-0.5-0.38-1.03-0.7-1.62-0.94L14.4,2.81c-0.04-0.24-0.24-0.41-0.48-0.41 h-3.84c-0.24,0-0.43,0.17-0.47,0.41L9.25,5.35C8.66,5.59,8.12,5.92,7.63,6.29L5.24,5.33c-0.22-0.08-0.47,0-0.59,0.22L2.73,8.87 C2.62,9.08,2.66,9.34,2.86,9.48l2.03,1.58C4.84,11.36,4.8,11.69,4.8,12s0.02,0.64,0.06,0.94l-2.03,1.58 c-0.18,0.14-0.23,0.41-0.12,0.61l1.92,3.32c0.12,0.22,0.37,0.29,0.59,0.22l2.39-0.96c0.5,0.38,1.03,0.7,1.62,0.94l0.36,2.54 c0.05,0.24,0.24,0.41,0.48,0.41h3.84c0.24,0,0.43-0.17,0.47-0.41l0.36-2.54c0.59-0.24,1.13-0.56,1.62-0.94l2.39,0.96 c0.22,0.08,0.47,0,0.59-0.22l1.92-3.32c0.12-0.22,0.07-0.49-0.12-0.61L19.14,12.94z M12,15.6c-1.98,0-3.6-1.62-3.6-3.6 s1.62-3.6,3.6-3.6s3.6,1.62,3.6,3.6S13.98,15.6,12,15.6z"/>
</svg>"""

class PlayerListPage(QWidget):
    settingsRequested = pyqtSignal()
    recordSelected = pyqtSignal(str)

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        
        layout = QVBoxLayout(self)
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
        
        self.refresh_list()

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
                print(f"[PlayerListPage] Error saving custom name: {e}")

    def clear_list(self):
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def refresh_list(self):
        self.clear_list()
                
        if not os.path.exists(self.config.SAVE_DIR):
            return
            
        records_by_date = {}
        riot_id = getattr(self.config, "RIOT_ID", "")
        tag_line = getattr(self.config, "TAG_LINE", "")
            
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
                        agent_name = get_agent_name(riot_id, tag_line, match_info, kills_data)
                        
                        display_name = f"{mode} - {map_name} - {agent_name} - {date_key} {time_str}"
                    
                    result = get_match_result(riot_id, tag_line, match_info, kills_data)
                    video_path = find_video_for_json(self.config.SAVE_DIR, f, data)
                    
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
                    print(f"[PlayerListPage] Error loading {f}: {e}")
                    
        for date_key in sorted(records_by_date.keys(), reverse=True):
            date_label = QLabel(date_key)
            date_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #FF4655; margin-top: 15px; margin-bottom: 5px;")
            self.scroll_layout.addWidget(date_label)
            
            flow_widget = QWidget()
            flow_layout = FlowLayout(flow_widget)
            
            for rec in records_by_date[date_key]:
                item_widget = RecordItemWidget(rec['filename'], rec['display_name'], rec['thumb_path'], rec['result'])
                item_widget.doubleClicked.connect(self.recordSelected.emit)
                item_widget.renameRequested.connect(self.rename_record)
                flow_layout.addWidget(item_widget)
                
            self.scroll_layout.addWidget(flow_widget)
            
        self.scroll_layout.addStretch()