from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtMultimediaWidgets import QVideoWidget

class ClickableVideoWidget(QVideoWidget):
    clicked = pyqtSignal()
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

class PlayerContainer(QWidget):
    def __init__(self, video_widget, aspect_ratio=16/9, parent=None):
        super().__init__(parent)
        self.aspect_ratio = aspect_ratio
        self.video_widget = video_widget
        self.video_widget.setParent(self)

    def resizeEvent(self, event):
        w = event.size().width()
        h = event.size().height()
        
        if h > 0:
            if w / h > self.aspect_ratio:
                new_video_h = h
                new_video_w = int(new_video_h * self.aspect_ratio)
                x = (w - new_video_w) // 2
                y = 0
            else:
                new_video_w = w
                new_video_h = int(new_video_w / self.aspect_ratio)
                x = 0
                y = (h - new_video_h) // 2
                
            self.video_widget.setGeometry(x, y, new_video_w, new_video_h)
            
        super().resizeEvent(event)

