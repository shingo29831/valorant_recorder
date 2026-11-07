"""Module: @role: システムトレイアイコンの生成、コンテキストメニュー構築、トレイイベント処理を統括するマネージャ"""

from typing import Callable, Optional
from PyQt6.QtCore import QObject
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMainWindow, QMenu, QSystemTrayIcon

from core.version import APP_VERSION
from ui.window_utils import get_resource_path


class SystemTrayManager(QObject):
    """常駐システムトレイアイコンの表示および右クリックメニューの挙動を管理する"""

    def __init__(
        self,
        window: QMainWindow,
        t: object,
        on_show: Callable[[], None],
        on_spec: Callable[[], None],
        on_tutorial: Callable[[], None],
        on_quit: Callable[[], None],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent or window)
        self._window = window
        self._t = t
        self._on_show = on_show
        self._on_spec = on_spec
        self._on_tutorial = on_tutorial
        self._on_quit = on_quit
        self.tray_icon: Optional[QSystemTrayIcon] = None
        self.setup_tray_icon()

    def setup_tray_icon(self) -> None:
        """トレイアイコンと右クリックメニューを生成して表示する"""
        self.tray_icon = QSystemTrayIcon(self._window)
        self.tray_icon.setIcon(QIcon(get_resource_path("assets/icon.ico")))
        self.tray_icon.setToolTip(f"ValoReco ヴァロレコ v{APP_VERSION}")

        tray_menu = QMenu()
        show_action = QAction("Show", self._window)
        show_action.triggered.connect(self._on_show)
        spec_action = QAction(getattr(self._t, 'spec_dialog_title', "Recommended Settings"), self._window)
        spec_action.triggered.connect(self._on_spec)
        tutorial_action = QAction(getattr(self._t, 'tutorial_title', "Tutorial"), self._window)
        tutorial_action.triggered.connect(self._on_tutorial)
        quit_action = QAction("Quit", self._window)
        quit_action.triggered.connect(self._on_quit)

        tray_menu.addAction(show_action)
        tray_menu.addAction(spec_action)
        tray_menu.addAction(tutorial_action)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.handle_activation)
        self.tray_icon.show()

    def handle_activation(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """トレイアイコンのダブルクリック時にメインウィンドウを前面復帰させる"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._on_show()

    def hide(self) -> None:
        # アプリ終了時にタスクバーへゾンビアイコンが残存するのを防止
        if self.tray_icon:
            self.tray_icon.hide()
