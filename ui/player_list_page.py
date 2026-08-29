import os
import json
import subprocess
import re
from datetime import datetime
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QScrollArea, QLabel, QInputDialog, QMessageBox, QMenu
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QByteArray, QPoint
from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer
from core.config import Config
from ui.player_components import FlowLayout, RecordItemWidget
from ui.player_utils import find_video_for_json, get_agent_name, get_match_result

SETTINGS_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M19.14,12.94c0.04-0.3,0.06-0.61,0.06-0.94c0-0.32-0.02-0.64-0.06-0.94l2.03-1.58c0.18-0.14,0.23-0.41,0.12-0.61 l-1.92-3.32c-0.12-0.22-0.37-0.29-0.59-0.22l-2.39,0.96c-0.5-0.38-1.03-0.7-1.62-0.94L14.4,2.81c-0.04-0.24-0.24-0.41-0.48-0.41 h-3.84c-0.24,0-0.43,0.17-0.47,0.41L9.25,5.35C8.66,5.59,8.12,5.92,7.63,6.29L5.24,5.33c-0.22-0.08-0.47,0-0.59,0.22L2.73,8.87 C2.62,9.08,2.66,9.34,2.86,9.48l2.03,1.58C4.84,11.36,4.8,11.69,4.8,12s0.02,0.64,0.06,0.94l-2.03,1.58 c-0.18,0.14-0.23,0.41-0.12,0.61l1.92,3.32c0.12,0.22,0.37,0.29,0.59,0.22l2.39-0.96c0.5,0.38,1.03,0.7,1.62,0.94l0.36,2.54 c0.05,0.24,0.24,0.41,0.48,0.41h3.84c0.24,0,0.43-0.17,0.47-0.41l0.36-2.54c0.59-0.24,1.13-0.56,1.62-0.94l2.39,0.96 c0.22,0.08,0.47,0,0.59-0.22l1.92-3.32c0.12-0.22,0.07-0.49-0.12-0.61L19.14,12.94z M12,15.6c-1.98,0-3.6-1.62-3.6-3.6 s1.62-3.6,3.6-3.6s3.6,1.62,3.6,3.6S13.98,15.6,12,15.6z"/>
</svg>"""

TRASH_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M16 9v10H8V9h8m-1.5-6h-5l-1 1H5v2h14V4h-3.5l-1-1zM18 7H6v12c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7z"/>
</svg>"""

STAR_OUTLINE_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="transparent" stroke="white" stroke-width="2">
  <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
</svg>"""

FILTER_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M10 18h4v-2h-4v2zM3 6v2h18V6H3zm3 7h12v-2H6v2z"/>
</svg>"""

class PlayerListPage(QWidget):
    settingsRequested = pyqtSignal()
    recordSelected = pyqtSignal(str)

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        
        self.delete_mode = False
        self.favorite_mode = False
        self.current_filter = None
        self.available_agents = set()
        self.available_maps = set()
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        
        header_widget = QWidget()
        header_widget.setStyleSheet("background-color: transparent; border: none;")
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addStretch()
        
        self.trash_btn = QPushButton()
        self.trash_btn.setFixedSize(30, 30)
        self.trash_btn.setCheckable(True)
        self.trash_btn.setStyleSheet("""
            QPushButton { background-color: transparent; border: none; border-radius: 15px; }
            QPushButton:checked { background-color: rgba(255, 70, 85, 0.5); }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); }
        """)
        
        trash_pixmap = QPixmap(24, 24)
        trash_pixmap.fill(Qt.GlobalColor.transparent)
        trash_painter = QPainter(trash_pixmap)
        trash_renderer = QSvgRenderer(QByteArray(TRASH_SVG))
        trash_renderer.render(trash_painter)
        trash_painter.end()
        
        self.trash_btn.setIcon(QIcon(trash_pixmap))
        self.trash_btn.setIconSize(QSize(20, 20))
        self.trash_btn.clicked.connect(self.toggle_delete_mode)
        
        header_layout.addWidget(self.trash_btn)
        
        self.fav_btn = QPushButton()
        self.fav_btn.setFixedSize(30, 30)
        self.fav_btn.setCheckable(True)
        self.fav_btn.setStyleSheet("""
            QPushButton { background-color: transparent; border: none; border-radius: 15px; }
            QPushButton:checked { background-color: rgba(255, 215, 0, 0.5); }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); }
        """)
        
        fav_pixmap = QPixmap(24, 24)
        fav_pixmap.fill(Qt.GlobalColor.transparent)
        fav_painter = QPainter(fav_pixmap)
        fav_renderer = QSvgRenderer(QByteArray(STAR_OUTLINE_SVG))
        fav_renderer.render(fav_painter)
        fav_painter.end()
        
        self.fav_btn.setIcon(QIcon(fav_pixmap))
        self.fav_btn.setIconSize(QSize(20, 20))
        self.fav_btn.clicked.connect(self.toggle_favorite_mode)
        
        header_layout.addWidget(self.fav_btn)
        
        self.filter_btn = QPushButton()
        self.filter_btn.setFixedSize(30, 30)
        self.filter_btn.setStyleSheet("""
            QPushButton { background-color: rgba(128, 128, 128, 0.5); border: none; border-radius: 15px; }
            QPushButton:hover { background-color: rgba(128, 128, 128, 0.8); }
        """)
        
        filter_pixmap = QPixmap(24, 24)
        filter_pixmap.fill(Qt.GlobalColor.transparent)
        filter_painter = QPainter(filter_pixmap)
        filter_renderer = QSvgRenderer(QByteArray(FILTER_SVG))
        filter_renderer.render(filter_painter)
        filter_painter.end()
        
        self.filter_btn.setIcon(QIcon(filter_pixmap))
        self.filter_btn.setIconSize(QSize(18, 18))
        self.filter_btn.clicked.connect(self.show_filter_menu)
        
        header_layout.addWidget(self.filter_btn)
        
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
        
        header_layout.addWidget(settings_btn)
        
        layout.addWidget(header_widget)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background-color: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.scroll_area.setWidget(self.scroll_content)
        
        layout.addWidget(self.scroll_area)
        
        self.refresh_list()

    def _get_selected_files(self):
        selected_files = []
        for i in range(self.scroll_layout.count()):
            item = self.scroll_layout.itemAt(i)
            widget = item.widget()
            if widget and isinstance(widget, QWidget):
                layout = widget.layout()
                if isinstance(layout, FlowLayout):
                    for j in range(layout.count()):
                        flow_item = layout.itemAt(j)
                        if flow_item and flow_item.widget() and isinstance(flow_item.widget(), RecordItemWidget):
                            record_widget = flow_item.widget()
                            if record_widget.is_checked():
                                selected_files.append(record_widget.json_filename)
        return selected_files

    def _update_items_selection_mode(self):
        mode = "delete" if self.delete_mode else "favorite"
        enabled = self.delete_mode or self.favorite_mode
        for i in range(self.scroll_layout.count()):
            item = self.scroll_layout.itemAt(i)
            widget = item.widget()
            if widget and isinstance(widget, QWidget):
                if widget.property("is_date_header"):
                    layout = widget.layout()
                    cb_item = layout.itemAt(0)
                    if cb_item and cb_item.widget() and isinstance(cb_item.widget(), QPushButton):
                        cb = cb_item.widget()
                        cb.setVisible(enabled)
                        if not enabled:
                            cb.blockSignals(True)
                            cb.setChecked(False)
                            self._update_date_checkbox_style(cb, False, mode)
                            cb.blockSignals(False)
                        else:
                            self._update_date_checkbox_style(cb, cb.isChecked(), mode)
                else:
                    layout = widget.layout()
                    if isinstance(layout, FlowLayout):
                        for j in range(layout.count()):
                            flow_item = layout.itemAt(j)
                            if flow_item and flow_item.widget() and isinstance(flow_item.widget(), RecordItemWidget):
                                flow_item.widget().set_selection_mode(enabled, mode)

    def _update_date_checkbox_style(self, checkbox, checked, mode="delete"):
        if checked:
            checkbox.setText("✓")
            color = "#FF4655" if mode == "delete" else "#FFD700"
            text_color = "white" if mode == "delete" else "black"
            checkbox.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color};
                    color: {text_color};
                    font-weight: bold;
                    border: 2px solid #FFFFFF;
                    border-radius: 4px;
                }}
            """)
        else:
            checkbox.setText("")
            checkbox.setStyleSheet("""
                QPushButton {
                    background-color: rgba(0, 0, 0, 0.6);
                    border: 2px solid #FFFFFF;
                    border-radius: 4px;
                }
            """)

    def _on_date_checkbox_toggled(self, checked, flow_widget, checkbox):
        mode = "delete" if self.delete_mode else "favorite"
        self._update_date_checkbox_style(checkbox, checked, mode)
        layout = flow_widget.layout()
        if isinstance(layout, FlowLayout):
            for j in range(layout.count()):
                flow_item = layout.itemAt(j)
                if flow_item and flow_item.widget() and isinstance(flow_item.widget(), RecordItemWidget):
                    flow_item.widget().set_checked(checked)

    def toggle_delete_mode(self, checked):
        if checked:
            self.delete_mode = True
            self.favorite_mode = False
            self.fav_btn.setChecked(False)
            self._update_items_selection_mode()
        else:
            selected_files = self._get_selected_files()
            if selected_files:
                reply = QMessageBox.question(self, 'Delete Records', 
                                             f'Are you sure you want to delete {len(selected_files)} selected record(s)?',
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                             QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    for json_filename in selected_files:
                        self._delete_single_record_files(json_filename)
                    self.delete_mode = False
                    self._update_items_selection_mode()
                    self.refresh_list()
                else:
                    self.trash_btn.blockSignals(True)
                    self.trash_btn.setChecked(True)
                    self.trash_btn.blockSignals(False)
            else:
                self.delete_mode = False
                self._update_items_selection_mode()

    def toggle_favorite_mode(self, checked):
        if checked:
            self.favorite_mode = True
            self.delete_mode = False
            self.trash_btn.setChecked(False)
            self._update_items_selection_mode()
        else:
            selected_files = self._get_selected_files()
            if selected_files:
                for json_filename in selected_files:
                    self._toggle_favorite_status(json_filename)
            self.favorite_mode = False
            self._update_items_selection_mode()
            self.refresh_list()

    def _toggle_favorite_status(self, json_filename):
        json_path = os.path.join(self.config.SAVE_DIR, json_filename)
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data["is_favorite"] = not data.get("is_favorite", False)
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[PlayerListPage] Error updating favorite status: {e}")

    def show_filter_menu(self):
        menu = QMenu(self)
        
        action_clear = menu.addAction("Clear Filter")
        action_clear.triggered.connect(lambda: self.apply_filter(None))
        menu.addSeparator()
        
        action_fav = menu.addAction("Favorite")
        action_fav.triggered.connect(lambda: self.apply_filter(("favorite", True)))
        
        menu_agent = menu.addMenu("Agent")
        for agent in sorted(self.available_agents):
            action = menu_agent.addAction(agent)
            action.triggered.connect(lambda checked, a=agent: self.apply_filter(("agent", a)))
            
        menu_result = menu.addMenu("Result")
        for res, label in [("win", "Win"), ("loss", "Loss"), ("draw", "Draw")]:
            action = menu_result.addAction(label)
            action.triggered.connect(lambda checked, r=res: self.apply_filter(("result", r)))
            
        menu_map = menu.addMenu("Map")
        for m in sorted(self.available_maps):
            action = menu_map.addAction(m)
            action.triggered.connect(lambda checked, m_name=m: self.apply_filter(("map", m_name)))
            
        menu.exec(self.filter_btn.mapToGlobal(QPoint(0, self.filter_btn.height())))

    def apply_filter(self, filter_tuple):
        self.current_filter = filter_tuple
        self.refresh_list()

    def _delete_single_record_files(self, json_filename):
        json_path = os.path.join(self.config.SAVE_DIR, json_filename)
        try:
            if os.path.exists(json_path):
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                video_path = find_video_for_json(self.config.SAVE_DIR, json_filename, data)
                thumb_path = json_path.replace('.json', '.jpg')
                
                os.remove(json_path)
                if video_path and os.path.exists(video_path):
                    os.remove(video_path)
                if os.path.exists(thumb_path):
                    os.remove(thumb_path)
        except Exception as e:
            print(f"[PlayerListPage] Error deleting record {json_filename}: {e}")

    def delete_record(self, json_filename):
        reply = QMessageBox.question(self, 'Delete Record', 
                                     'Are you sure you want to delete this record?',
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._delete_single_record_files(json_filename)
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
        
        self.available_agents.clear()
        self.available_maps.clear()
            
        for f in sorted(os.listdir(self.config.SAVE_DIR), reverse=True):
            if f.endswith(".json"):
                json_path = os.path.join(self.config.SAVE_DIR, f)
                try:
                    with open(json_path, 'r', encoding='utf-8') as jf:
                        data = json.load(jf)
                    
                    match_info = data.get("match_info", data)
                    custom_name = data.get("custom_name")
                    is_favorite = data.get("is_favorite", False)
                    
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
                    
                    mode = match_info.get("metadata", {}).get("mode", "Unknown")
                    map_name = match_info.get("metadata", {}).get("map", "Unknown")
                    agent_name = get_agent_name(riot_id, tag_line, match_info, kills_data)
                    result = get_match_result(riot_id, tag_line, match_info, kills_data)
                    
                    self.available_agents.add(agent_name)
                    self.available_maps.add(map_name)
                    
                    if self.current_filter:
                        f_type, f_val = self.current_filter
                        if f_type == "favorite" and not is_favorite:
                            continue
                        elif f_type == "agent" and agent_name != f_val:
                            continue
                        elif f_type == "result" and result != f_val:
                            continue
                        elif f_type == "map" and map_name != f_val:
                            continue

                    if custom_name:
                        display_name = custom_name
                    else:
                        display_name = f"{mode} - {map_name} - {agent_name} - {date_key} {time_str}"
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
                            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
                            
                    if date_key not in records_by_date:
                        records_by_date[date_key] = []
                        
                    records_by_date[date_key].append({
                        'filename': f,
                        'display_name': display_name,
                        'thumb_path': thumb_path if os.path.exists(thumb_path) else "",
                        'result': result,
                        'is_favorite': is_favorite
                    })
                    
                except Exception as e:
                    print(f"[PlayerListPage] Error loading {f}: {e}")
                    
        for date_key in sorted(records_by_date.keys(), reverse=True):
            date_header_widget = QWidget()
            date_header_widget.setProperty("is_date_header", True)
            date_header_layout = QHBoxLayout(date_header_widget)
            date_header_layout.setContentsMargins(0, 15, 0, 5)
            
            date_checkbox = QPushButton()
            date_checkbox.setCheckable(True)
            date_checkbox.setFixedSize(20, 20)
            date_checkbox.setStyleSheet("""
                QPushButton {
                    background-color: rgba(0, 0, 0, 0.6);
                    border: 2px solid #FFFFFF;
                    border-radius: 4px;
                }
            """)
            date_checkbox.setText("")
            date_checkbox.setVisible(self.delete_mode or self.favorite_mode)
            
            date_label = QLabel(date_key)
            date_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #FF4655;")
            
            date_header_layout.addWidget(date_checkbox)
            date_header_layout.addWidget(date_label)
            date_header_layout.addStretch()
            
            self.scroll_layout.addWidget(date_header_widget)
            
            flow_widget = QWidget()
            flow_layout = FlowLayout(flow_widget)
            
            date_checkbox.toggled.connect(lambda checked, fw=flow_widget, cb=date_checkbox: self._on_date_checkbox_toggled(checked, fw, cb))
            
            for rec in records_by_date[date_key]:
                item_widget = RecordItemWidget(rec['filename'], rec['display_name'], rec['thumb_path'], rec['result'], rec['is_favorite'])
                item_widget.doubleClicked.connect(self.recordSelected.emit)
                item_widget.renameRequested.connect(self.rename_record)
                item_widget.deleteRequested.connect(self.delete_record)
                item_widget.set_selection_mode(self.delete_mode or self.favorite_mode, "delete" if self.delete_mode else "favorite")
                flow_layout.addWidget(item_widget)
                
            self.scroll_layout.addWidget(flow_widget)
            
        self.scroll_layout.addStretch()