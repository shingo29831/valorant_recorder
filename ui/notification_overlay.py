"""Module: @role: ゲームのフォーカスを奪わずにプレイ画面の左上へ通知を表示するオーバーレイ"""
import sys
from typing import Optional, Tuple
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PyQt6.QtGui import QGuiApplication, QScreen

OFFSCREEN_POS = -9999
OVERLAY_MARGIN = 30
MAX_OPACITY = 0.92
ANIMATION_DURATION_MS = 300

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
MONITOR_DEFAULTTONEAREST = 2


class NotificationOverlay(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background-color: #0F1923;")

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.label = QLabel("")
        self.label.setStyleSheet("""
            QLabel {
                background-color: #0F1923;
                color: #ECE8E1;
                border-left: 4px solid #FF4655;
                padding: 12px 20px;
                font-family: sans-serif;
                font-size: 14px;
                font-weight: bold;
            }
        """)
        self.layout.addWidget(self.label)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.fade_out)

        self.opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        self.opacity_anim.setDuration(ANIMATION_DURATION_MS)
        # Nuitka環境でのdisconnect例外を防ぐため常時接続しフラグで制御する
        self._is_fading_out = False
        self.opacity_anim.finished.connect(self._on_fade_out_finished)

        # show/hide切り替えによるアクティブ化を防ぐため常時表示状態で画面外に待機させる
        self.setWindowOpacity(0.0)
        self.move(OFFSCREEN_POS, OFFSCREEN_POS)
        self.show()
        self._apply_win32_overlay_styles()

    def _find_valorant_hwnd(self) -> int:
        if sys.platform != "win32":
            return 0
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL

        for title in ("VALORANT  ", "VALORANT"):
            hwnd = user32.FindWindowW("UnrealWindow", title)
            if hwnd and user32.IsWindowVisible(hwnd):
                return int(hwnd)

        found_hwnd = 0
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int

        def _enum_proc(hwnd: int, _lparam: int) -> bool:
            nonlocal found_hwnd
            if not hwnd or not user32.IsWindowVisible(hwnd):
                return True
            class_buf = ctypes.create_unicode_buffer(64)
            if user32.GetClassNameW(hwnd, class_buf, 64) > 0 and class_buf.value == "UnrealWindow":
                title_buf = ctypes.create_unicode_buffer(128)
                user32.GetWindowTextW(hwnd, title_buf, 128)
                if title_buf.value.strip().upper().startswith("VALORANT"):
                    found_hwnd = int(hwnd)
                    return False
            return True

        enum_cb = WNDENUMPROC(_enum_proc)
        user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.EnumWindows(enum_cb, 0)
        return found_hwnd

    def _resolve_target_position(self) -> Tuple[int, int, Optional[QScreen]]:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class MONITORINFOEXW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32),
                ]

            user32 = ctypes.windll.user32
            user32.GetForegroundWindow.argtypes = []
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
            user32.MonitorFromWindow.restype = wintypes.HMONITOR
            user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
            user32.GetMonitorInfoW.restype = wintypes.BOOL
            user32.IsIconic.argtypes = [wintypes.HWND]
            user32.IsIconic.restype = wintypes.BOOL
            user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
            user32.ClientToScreen.restype = wintypes.BOOL
            user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            user32.GetClientRect.restype = wintypes.BOOL

            valorant_hwnd = self._find_valorant_hwnd()
            target_hwnd = valorant_hwnd or user32.GetForegroundWindow()

            if target_hwnd:
                hmonitor = user32.MonitorFromWindow(target_hwnd, MONITOR_DEFAULTTONEAREST)
                if hmonitor:
                    mon_info = MONITORINFOEXW()
                    mon_info.cbSize = ctypes.sizeof(MONITORINFOEXW)
                    if user32.GetMonitorInfoW(hmonitor, ctypes.byref(mon_info)):
                        rc_mon = mon_info.rcMonitor
                        phys_x = rc_mon.left
                        phys_y = rc_mon.top

                        if valorant_hwnd and not user32.IsIconic(valorant_hwnd):
                            pt = wintypes.POINT(0, 0)
                            client_rect = wintypes.RECT()
                            if (
                                user32.ClientToScreen(valorant_hwnd, ctypes.byref(pt))
                                and user32.GetClientRect(valorant_hwnd, ctypes.byref(client_rect))
                                and client_rect.right > 0
                                and client_rect.bottom > 0
                            ):
                                if rc_mon.left <= pt.x < rc_mon.right and rc_mon.top <= pt.y < rc_mon.bottom:
                                    phys_x = pt.x
                                    phys_y = pt.y

                        target_screen = None
                        for s in QGuiApplication.screens():
                            if s.name() == mon_info.szDevice:
                                target_screen = s
                                break
                        if target_screen is None:
                            center_pt = QPoint(
                                (rc_mon.left + rc_mon.right) // 2,
                                (rc_mon.top + rc_mon.bottom) // 2,
                            )
                            target_screen = QGuiApplication.screenAt(center_pt)

                        if target_screen is not None:
                            dpr = target_screen.devicePixelRatio() or 1.0
                            geom = target_screen.geometry()
                            logical_x = geom.x() + int((phys_x - rc_mon.left) / dpr) + OVERLAY_MARGIN
                            logical_y = geom.y() + int((phys_y - rc_mon.top) / dpr) + OVERLAY_MARGIN
                            return logical_x, logical_y, target_screen

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geom = screen.geometry()
            return geom.x() + OVERLAY_MARGIN, geom.y() + OVERLAY_MARGIN, screen
        return OVERLAY_MARGIN, OVERLAY_MARGIN, None

    def _apply_win32_overlay_styles(self) -> None:
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = wintypes.HWND(int(self.winId()))

        get_window_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_window_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
        get_window_long.restype = ctypes.c_ssize_t
        set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_window_long.restype = ctypes.c_ssize_t

        ex_style = get_window_long(hwnd, GWL_EXSTYLE)
        target_style = ex_style | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
        if ex_style != target_style:
            set_window_long(hwnd, GWL_EXSTYLE, target_style)

        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL

        # 位置とサイズはQtの論理座標系に委ね、Zオーダーのみ非アクティブで最前面化する
        user32.SetWindowPos(
            hwnd,
            wintypes.HWND(-1),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
        )

    def show_message(self, message: str, duration: int = 3000) -> None:
        self._is_fading_out = False
        if self.opacity_anim.state() == QPropertyAnimation.State.Running:
            self.opacity_anim.stop()

        x, y, target_screen = self._resolve_target_position()
        if target_screen is not None and self.windowHandle() is not None:
            self.windowHandle().setScreen(target_screen)

        self.label.setText(message)
        self.move(x, y)
        self.adjustSize()
        self.move(x, y)
        self._apply_win32_overlay_styles()
        self.label.repaint()
        self.repaint()

        # 試合開始時のゲーム画面遷移でZオーダーが下がるのを防ぐため遅延再適用する
        QTimer.singleShot(150, self._apply_win32_overlay_styles)
        QTimer.singleShot(500, self._apply_win32_overlay_styles)

        self.opacity_anim.setStartValue(self.windowOpacity())
        self.opacity_anim.setEndValue(MAX_OPACITY)
        self.opacity_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.opacity_anim.start()

        self.timer.start(duration)

    def fade_out(self) -> None:
        if self.opacity_anim.state() == QPropertyAnimation.State.Running:
            self.opacity_anim.stop()
        self._is_fading_out = True
        self.opacity_anim.setStartValue(self.windowOpacity())
        self.opacity_anim.setEndValue(0.0)
        self.opacity_anim.setEasingCurve(QEasingCurve.Type.InQuad)
        self.opacity_anim.start()

    def _on_fade_out_finished(self) -> None:
        if not self._is_fading_out:
            return
        self._is_fading_out = False
        self.move(OFFSCREEN_POS, OFFSCREEN_POS)
