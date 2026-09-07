from PyQt6.QtWidgets import QLabel, QGraphicsOpacityEffect
from PyQt6.QtCore import QTimer, QPropertyAnimation

class InAppNotification(QLabel):
    """別ウィンドウを作らず、親ウィジェット内に直接描画する通知ラベル（フォーカススティーリング防止）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
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
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.effect.setOpacity(0.0)
        self.hide()
        
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.fade_out)
        
        self.opacity_anim = QPropertyAnimation(self.effect, b"opacity")
        self.opacity_anim.setDuration(300)
        
    def show_message(self, message, duration=3000):
        if self.opacity_anim.state() == QPropertyAnimation.State.Running:
            self.opacity_anim.stop()
            try:
                self.opacity_anim.finished.disconnect(self._on_fade_out_finished)
            except TypeError:
                pass
                
        self.setText(message)
        self.adjustSize()
        
        if self.parent():
            parent_rect = self.parent().rect()
            self.move((parent_rect.width() - self.width()) // 2, 20)
            
        self.show()
        self.raise_()
        
        self.opacity_anim.setStartValue(self.effect.opacity())
        self.opacity_anim.setEndValue(1.0)
        self.opacity_anim.start()
        self.timer.start(duration)
        
    def fade_out(self):
        self.opacity_anim.setStartValue(self.effect.opacity())
        self.opacity_anim.setEndValue(0.0)
        self.opacity_anim.finished.connect(self._on_fade_out_finished)
        self.opacity_anim.start()
        
    def _on_fade_out_finished(self):
        try:
            self.opacity_anim.finished.disconnect(self._on_fade_out_finished)
        except TypeError:
            pass
        self.hide()
