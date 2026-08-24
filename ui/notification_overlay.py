from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QGuiApplication

class NotificationOverlay(QWidget):
    def __init__(self):
        super().__init__()
        # 最前面表示、フレームなし、タスクバー非表示、クリック透過を設定
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        self.label = QLabel("")
        self.label.setStyleSheet("""
            QLabel {
                background-color: rgba(15, 25, 35, 220);
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
        self.opacity_anim.setDuration(300)
        
    def show_message(self, message, duration=3000):
        # アニメーション中の場合は一度停止してリセット
        if self.opacity_anim.state() == QPropertyAnimation.State.Running:
            self.opacity_anim.stop()
            try:
                self.opacity_anim.finished.disconnect(self._on_fade_out_finished)
            except TypeError:
                pass

        self.label.setText(message)
        self.adjustSize()
        
        # プライマリスクリーンの左上に配置
        screen = QGuiApplication.primaryScreen()
        if screen:
            geom = screen.geometry()
            self.move(geom.x() + 30, geom.y() + 30)
            
        self.setWindowOpacity(0.0)
        self.show()
        
        self.opacity_anim.setStartValue(0.0)
        self.opacity_anim.setEndValue(1.0)
        self.opacity_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.opacity_anim.start()
        
        self.timer.start(duration)
        
    def fade_out(self):
        self.opacity_anim.setStartValue(self.windowOpacity())
        self.opacity_anim.setEndValue(0.0)
        self.opacity_anim.setEasingCurve(QEasingCurve.Type.InQuad)
        self.opacity_anim.finished.connect(self._on_fade_out_finished)
        self.opacity_anim.start()
        
    def _on_fade_out_finished(self):
        try:
            self.opacity_anim.finished.disconnect(self._on_fade_out_finished)
        except TypeError:
            pass
        self.hide()
