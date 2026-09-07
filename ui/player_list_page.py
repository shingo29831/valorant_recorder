import os
import json
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QScrollArea, QLabel, QInputDialog, QMessageBox, QMenu
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QByteArray, QPoint
from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer
from core.config import Config
from core.i18n import get_trans
from ui.flow_layout import FlowLayout
from ui.record_item_widget import RecordItemWidget
from ui.player_utils import find_video_for_json
from ui.record_data_loader import RecordDataLoader
from ui.player_list_header import PlayerListHeader

FILTER_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M10 18h4v-2h-4v2zM3 6v2h18V6H3zm3 7h12v-2H6v2z"/>
</svg>"""

SORT_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M3 18h6v-2H3v2zM3 6v2h18V6H3zm0 7h12v-2H3v2z"/>
</svg>"""

class PlayerListPage(QWidget):
    settingsRequested = pyqtSignal()
    recordSelected = pyqtSignal(str)

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.t = get_trans(getattr(self.config, 'LANGUAGE', 'en'))
        
        self.delete_mode = False
        self.favorite_mode = False
        self.current_filter = None
        self.current_sort = "date_desc"
        self.current_mode = "All"
        self.available_agents = set()
        self.available_maps = set()
        self._needs_refresh = False
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        
        self.header = PlayerListHeader(self.t, self)
        self.header.tabChanged.connect(self._on_tab_clicked)
        self.header.deleteModeToggled.connect(self.toggle_delete_mode)
        self.header.favoriteModeToggled.connect(self.toggle_favorite_mode)
        self.header.settingsRequested.connect(self.settingsRequested.emit)
        layout.addWidget(self.header)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("""
            QScrollArea { 
                border: none; 
                background-color: transparent; 
            }
            QScrollBar:vertical {
                background: #1A1A1A;
                width: 12px;
                margin: 0px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: #555555;
                min-height: 20px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background: #777777;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
        
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background-color: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.scroll_area.setWidget(self.scroll_content)
        
        layout.addWidget(self.scroll_area)
        
        # フィルターボタンをスクロールエリア上のオーバーレイとして配置
        self.filter_btn = QPushButton(self)
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
        self.filter_btn.raise_()
        
        # ソートボタンをスクロールエリア上のオーバーレイとして配置
        self.sort_btn = QPushButton(self)
        self.sort_btn.setFixedSize(30, 30)
        self.sort_btn.setStyleSheet("""
            QPushButton { background-color: rgba(128, 128, 128, 0.5); border: none; border-radius: 15px; }
            QPushButton:hover { background-color: rgba(128, 128, 128, 0.8); }
        """)
        
        sort_pixmap = QPixmap(24, 24)
        sort_pixmap.fill(Qt.GlobalColor.transparent)
        sort_painter = QPainter(sort_pixmap)
        sort_renderer = QSvgRenderer(QByteArray(SORT_SVG))
        sort_renderer.render(sort_painter)
        sort_painter.end()
        
        self.sort_btn.setIcon(QIcon(sort_pixmap))
        self.sort_btn.setIconSize(QSize(18, 18))
        self.sort_btn.clicked.connect(self.show_sort_menu)
        self.sort_btn.raise_()
        
        self.refresh_list()

    def _on_tab_clicked(self, mode):
        self.current_mode = mode
        self._apply_filters_and_render()

    def _apply_filters_and_render(self):
        self.clear_list()
        self.filtered_records = []
        
        if hasattr(self, 'all_loaded_records'):
            for rec in self.all_loaded_records:
                mode = rec.get('mode', 'Unknown')
                if self.current_mode != "All" and mode.lower() != self.current_mode.lower():
                    continue
                self.filtered_records.append(rec)
                
        # ソート処理
        if self.current_sort == "date_desc":
            self.filtered_records.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
        elif self.current_sort == "date_asc":
            self.filtered_records.sort(key=lambda x: x.get('timestamp', 0), reverse=False)
        elif self.current_sort == "mmr_desc":
            self.filtered_records.sort(key=lambda x: x.get('mmr_change', 0), reverse=True)
        elif self.current_sort == "mmr_asc":
            self.filtered_records.sort(key=lambda x: x.get('mmr_change', 0), reverse=False)
                
        self.displayed_count = 0
        self.current_group_key = None
        self.current_flow_layout = None
        
        try:
            self.scroll_area.verticalScrollBar().valueChanged.disconnect(self._on_scroll)
        except TypeError:
            pass
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll)
        
        self._load_more_items()

    def _on_scroll(self, value):
        scrollbar = self.scroll_area.verticalScrollBar()
        # 下から300px以内に近づいたら次のバッチを読み込む
        if scrollbar.maximum() - value < 300:
            self._load_more_items()

    def _load_more_items(self):
        if not hasattr(self, 'filtered_records'):
            return
            
        batch_size = 20
        start = self.displayed_count
        end = min(start + batch_size, len(self.filtered_records))
        
        if start >= end:
            return
            
        for i in range(start, end):
            rec = self.filtered_records[i]
            
            # ソート条件が日付以外の場合は、グループ化キーを固定してヘッダーを1つだけにする
            if self.current_sort.startswith("date"):
                group_key = rec['date_key']
            else:
                group_key = getattr(self.t, 'all_records', "All Records")
            
            if self.current_group_key != group_key:
                self.current_group_key = group_key
                
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
                
                date_label = QLabel(group_key)
                date_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #FF4655;")
                
                date_header_layout.addWidget(date_checkbox)
                date_header_layout.addWidget(date_label)
                date_header_layout.addStretch()
                
                self.scroll_layout.addWidget(date_header_widget)
                
                flow_widget = QWidget()
                self.current_flow_layout = FlowLayout(flow_widget)
                
                date_checkbox.toggled.connect(lambda checked, fw=flow_widget, cb=date_checkbox: self._on_date_checkbox_toggled(checked, fw, cb))
                
                self.scroll_layout.addWidget(flow_widget)
                
            item_widget = RecordItemWidget(
                rec['filename'], 
                rec['display_name'], 
                rec['thumb_path'], 
                rec['result'], 
                rec['is_favorite'], 
                rec.get('mmr_change', 0), 
                rec.get('party_members', []),
                rec.get('is_fetching_api', False)
            )
            item_widget.game_mode = rec.get('mode', 'Unknown')
            item_widget.doubleClicked.connect(self.recordSelected.emit)
            item_widget.renameRequested.connect(self.rename_record)
            item_widget.deleteRequested.connect(self.delete_record)
            item_widget.set_selection_mode(self.delete_mode or self.favorite_mode, "delete" if self.delete_mode else "favorite")
            
            self.current_flow_layout.addWidget(item_widget)
            
        self.displayed_count = end

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'scroll_area') and hasattr(self, 'filter_btn') and hasattr(self, 'sort_btn'):
            # スクロールエリアの右上に配置 (スクロールバーの幅を考慮して少し左に寄せる)
            x_filter = self.scroll_area.geometry().right() - self.filter_btn.width() - 25
            y = self.scroll_area.geometry().top() + 10
            self.filter_btn.move(x_filter, y)
            
            x_sort = x_filter - self.sort_btn.width() - 10
            self.sort_btn.move(x_sort, y)

    def showEvent(self, event):
        super().showEvent(event)
        # 保留されていたリスト更新があれば、画面が表示されたタイミングで実行する
        if getattr(self, '_needs_refresh', False):
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
            self.header.set_favorite_mode(False)
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
                    self.header.set_delete_mode(True)
            else:
                self.delete_mode = False
                self._update_items_selection_mode()

    def toggle_favorite_mode(self, checked):
        if checked:
            self.favorite_mode = True
            self.delete_mode = False
            self.header.set_delete_mode(False)
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
        
        action_clear = menu.addAction(self.t.filter_clear)
        action_clear.triggered.connect(lambda checked=False: self.apply_filter(None))
        menu.addSeparator()
        
        action_fav = menu.addAction(self.t.filter_favorite)
        action_fav.triggered.connect(lambda checked=False: self.apply_filter(("favorite", True)))
        
        menu_agent = menu.addMenu(self.t.filter_agent)
        for agent in sorted(self.available_agents):
            action = menu_agent.addAction(agent)
            action.triggered.connect(lambda checked, a=agent: self.apply_filter(("agent", a)))
            
        menu_result = menu.addMenu(self.t.filter_result)
        for res, label in [("win", self.t.result_win), ("loss", self.t.result_loss), ("draw", self.t.result_draw)]:
            action = menu_result.addAction(label)
            action.triggered.connect(lambda checked, r=res: self.apply_filter(("result", r)))
            
        menu_map = menu.addMenu(self.t.filter_map)
        for m in sorted(self.available_maps):
            action = menu_map.addAction(m)
            action.triggered.connect(lambda checked, m_name=m: self.apply_filter(("map", m_name)))
            
        menu.exec(self.filter_btn.mapToGlobal(QPoint(0, self.filter_btn.height())))

    def apply_filter(self, filter_tuple):
        self.current_filter = filter_tuple
        self.refresh_list()

    def show_sort_menu(self):
        menu = QMenu(self)
        
        sort_options = [
            ("date_desc", getattr(self.t, 'sort_date_desc', "日付 (新しい順)")),
            ("date_asc", getattr(self.t, 'sort_date_asc', "日付 (古い順)")),
            ("mmr_desc", getattr(self.t, 'sort_mmr_desc', "MMR変動 (高い順)")),
            ("mmr_asc", getattr(self.t, 'sort_mmr_asc', "MMR変動 (低い順)"))
        ]
        
        for sort_key, label in sort_options:
            action = menu.addAction(label)
            action.setCheckable(True)
            if self.current_sort == sort_key:
                action.setChecked(True)
            action.triggered.connect(lambda checked, s=sort_key: self.apply_sort(s))
            
        menu.exec(self.sort_btn.mapToGlobal(QPoint(0, self.sort_btn.height())))

    def apply_sort(self, sort_key):
        self.current_sort = sort_key
        self._apply_filters_and_render()

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
        # ウィンドウが非表示(ゲーム中など)の時にUIを再構築すると、
        # OSがフォーカス要求と誤認してゲームが裏画面に行くため、更新を保留する
        if not self.isVisible():
            self._needs_refresh = True
            return
            
        self._needs_refresh = False
        
        loader = RecordDataLoader(self.config)
        records_by_date, agents, maps = loader.load_records(self.current_filter)
        
        self.available_agents = agents
        self.available_maps = maps
        
        self.all_loaded_records = []
        for date_key in sorted(records_by_date.keys(), reverse=True):
            for rec in records_by_date[date_key]:
                rec['date_key'] = date_key
                self.all_loaded_records.append(rec)
                
        self._apply_filters_and_render()