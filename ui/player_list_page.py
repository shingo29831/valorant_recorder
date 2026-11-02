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
from ui.record_data_loader import RecordDataLoader
from ui.player_list_header import PlayerListHeader
from ui.player_list_renderer import PlayerListRenderer
from ui.player_list_actions import (
    delete_single_record_files,
    prompt_and_rename_record,
    show_record_filter_menu,
    show_record_sort_menu,
    toggle_favorite_status,
)

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
        self.header.settingsRequested.connect(self.settingsRequested)
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

        self.renderer = PlayerListRenderer(
            config=self.config,
            scroll_area=self.scroll_area,
            scroll_layout=self.scroll_layout,
            t=self.t,
            on_record_selected=self.recordSelected.emit,
            on_rename_requested=self.rename_record,
            on_delete_requested=self.delete_record,
        )
        
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
        filtered_records = []
        if hasattr(self, 'all_loaded_records'):
            for rec in self.all_loaded_records:
                mode = rec.get('mode', 'Unknown')
                if self.current_mode != "All" and mode.lower() != self.current_mode.lower():
                    continue
                filtered_records.append(rec)
                
        if self.current_sort == "date_desc":
            filtered_records.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
        elif self.current_sort == "date_asc":
            filtered_records.sort(key=lambda x: x.get('timestamp', 0), reverse=False)
        elif self.current_sort == "mmr_desc":
            filtered_records.sort(key=lambda x: x.get('mmr_change', 0), reverse=True)
        elif self.current_sort == "mmr_asc":
            filtered_records.sort(key=lambda x: x.get('mmr_change', 0), reverse=False)
                
        self.renderer.set_records(filtered_records, self.current_sort, self.delete_mode, self.favorite_mode)

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

    def toggle_delete_mode(self, checked):
        if checked:
            self.delete_mode = True
            self.favorite_mode = False
            self.header.set_favorite_mode(False)
            self.renderer.update_selection_mode(self.delete_mode, self.favorite_mode)
        else:
            selected_files = self.renderer.get_selected_files()
            if selected_files:
                reply = QMessageBox.question(
                    self,
                    "Delete Records",
                    f"Are you sure you want to delete {len(selected_files)} selected record(s)?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    for json_filename in selected_files:
                        self._delete_single_record_files(json_filename)
                    self.delete_mode = False
                    self.renderer.update_selection_mode(self.delete_mode, self.favorite_mode)
                    self.refresh_list()
                else:
                    self.header.set_delete_mode(True)
            else:
                self.delete_mode = False
                self.renderer.update_selection_mode(self.delete_mode, self.favorite_mode)

    def toggle_favorite_mode(self, checked):
        if checked:
            self.favorite_mode = True
            self.delete_mode = False
            self.header.set_delete_mode(False)
            self.renderer.update_selection_mode(self.delete_mode, self.favorite_mode)
        else:
            selected_files = self.renderer.get_selected_files()
            if selected_files:
                for json_filename in selected_files:
                    self._toggle_favorite_status(json_filename)
            self.favorite_mode = False
            self.renderer.update_selection_mode(self.delete_mode, self.favorite_mode)
            self.refresh_list()

    def _toggle_favorite_status(self, json_filename):
        toggle_favorite_status(self.config.SAVE_DIR, json_filename)

    def show_filter_menu(self):
        show_record_filter_menu(
            self, self.filter_btn, self.t, self.available_agents, self.available_maps, self.apply_filter
        )

    def apply_filter(self, filter_tuple):
        self.current_filter = filter_tuple
        self.refresh_list()

    def show_sort_menu(self):
        show_record_sort_menu(self, self.sort_btn, self.t, self.current_sort, self.apply_sort)

    def apply_sort(self, sort_key):
        self.current_sort = sort_key
        self._apply_filters_and_render()

    def _delete_single_record_files(self, json_filename):
        delete_single_record_files(self.config.SAVE_DIR, json_filename)

    def delete_record(self, json_filename):
        reply = QMessageBox.question(self, 'Delete Record', 
                                     'Are you sure you want to delete this record?',
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._delete_single_record_files(json_filename)
            self.refresh_list()

    def rename_record(self, json_filename, current_name):
        if prompt_and_rename_record(self, self.config.SAVE_DIR, json_filename, current_name):
            self.refresh_list()

    def clear_list(self):
        self.renderer.clear()

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