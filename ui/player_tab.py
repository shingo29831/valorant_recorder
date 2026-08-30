from PyQt6.QtWidgets import QWidget, QVBoxLayout, QStackedWidget
from PyQt6.QtCore import pyqtSignal
from core.config import Config
from ui.player_list_page import PlayerListPage
from ui.player_video_page import PlayerVideoPage

class PlayerTab(QWidget):
    settingsRequested = pyqtSignal()
    videoPageVisible = pyqtSignal(bool)

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        self.stacked_widget = QStackedWidget()
        self.layout.addWidget(self.stacked_widget)
        
        self.list_page = PlayerListPage(self.config)
        self.video_page = PlayerVideoPage(self.config)
        
        self.stacked_widget.addWidget(self.list_page)
        self.stacked_widget.addWidget(self.video_page)
        
        self.list_page.settingsRequested.connect(self.settingsRequested.emit)
        self.list_page.recordSelected.connect(self.on_record_selected)
        self.video_page.backRequested.connect(self.on_back_requested)
        
        self.stacked_widget.currentChanged.connect(self._on_current_changed)

    def _on_current_changed(self, index):
        self.videoPageVisible.emit(self.stacked_widget.currentWidget() == self.video_page)

    def on_record_selected(self, json_filename):
        self.video_page.load_recording(json_filename)
        self.stacked_widget.setCurrentWidget(self.video_page)

    def on_back_requested(self):
        self.stacked_widget.setCurrentWidget(self.list_page)

    def refresh_list(self):
        self.list_page.refresh_list()

    def on_hidden(self):
        # メモリ解放のため、非表示時に各ページのクリーンアップ処理を呼ぶ
        self.list_page.clear_list()
        self.video_page.cleanup_media()

    def on_shown(self):
        # 再表示時に必要なUIを再構築する
        if self.stacked_widget.currentWidget() == self.list_page:
            self.list_page.refresh_list()
        elif self.stacked_widget.currentWidget() == self.video_page:
            self.video_page.restore_media()
        self.videoPageVisible.emit(self.stacked_widget.currentWidget() == self.video_page)