import os
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSlider, QLayout, QMenu, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPoint, QRect, QSize, QByteArray, QRectF
from PyQt6.QtGui import QColor, QPen, QPixmap, QPainter
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtSvg import QSvgRenderer

KILL_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#00FF00">
  <path d="M21.7,2.3c-0.3-0.3-0.8-0.4-1.2-0.2c-0.4,0.2-0.7,0.6-0.7,1v1.6l-4.3,4.3l-1.4-1.4c-0.4-0.4-1-0.4-1.4,0l-1.4,1.4L2.6,0.3 C2.2-0.1,1.6-0.1,1.2,0.3S0.8,1.3,1.2,1.7l8.7,8.7l-1.4,1.4c-0.4,0.4-0.4,1,0,1.4l1.4,1.4l-4.3,4.3H4c-0.4,0-0.8,0.2-1,0.6 c-0.2,0.4-0.1,0.9,0.2,1.2l3,3c0.2,0.2,0.5,0.3,0.7,0.3c0.3,0,0.5-0.1,0.7-0.3c0.4-0.4,0.4-1,0-1.4l-1.6-1.6v-0.6l4.3-4.3l1.4,1.4 c0.4,0.4,1,0.4,1.4,0l1.4-1.4l8.7,8.7c0.2,0.2,0.5,0.3,0.7,0.3c0.3,0,0.5-0.1,0.7-0.3c0.4-0.4,0.4-1,0-1.4l-8.7-8.7l1.4-1.4 c0.4-0.4,0.4-1,0-1.4l-1.4-1.4l4.3-4.3V4c0-0.4,0.2-0.8,0.6-1c0.4-0.2,0.9-0.1,1.2,0.2l3,3c0.4,0.4,1,0.4,1.4,0s0.4-1,0-1.4 L21.7,2.3z"/>
</svg>"""

DEATH_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#FF0000">
  <path d="M12,2C6.48,2,2,6.48,2,12c0,4.84,3.44,8.87,8,9.8V22h4v-0.2c4.56-0.93,8-4.96,8-9.8C22,6.48,17.52,2,12,2z M8.5,14 C7.12,14,6,12.88,6,11.5S7.12,9,8.5,9S11,10.12,11,11.5S9.88,14,8.5,14z M15.5,14c-1.38,0-2.5-1.12-2.5-2.5S14.12,9,15.5,9 S18,10.12,18,11.5S16.88,14,15.5,14z M14,18h-4v-2h4V18z"/>
</svg>"""

ASSIST_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#00A2FF">
  <path d="M10,9H4C2.9,9,2,9.9,2,11v4c0,1.1,0.9,2,2,2h6c1.1,0,2-0.9,2-2v-4C12,9.9,11.1,9,10,9z M20,9h-6c-1.1,0-2,0.9-2,2v4 c0,1.1,0.9,2,2,2h6c1.1,0,2-0.9,2-2v-4C22,9.9,21.1,9,20,9z"/>
</svg>"""

class ClickableVideoWidget(QVideoWidget):
    clicked = pyqtSignal()
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=-1, hSpacing=10, vSpacing=10):
        super().__init__(parent)
        if margin != -1:
            self.setContentsMargins(margin, margin, margin, margin)
        self._hSpace = hSpacing
        self._vSpace = vSpacing
        self.itemList = []

    def addItem(self, item):
        self.itemList.append(item)

    def horizontalSpacing(self):
        return self._hSpace

    def verticalSpacing(self):
        return self._vSpace

    def count(self):
        return len(self.itemList)

    def itemAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.doLayout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.doLayout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.itemList:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def doLayout(self, rect, testOnly):
        x = rect.x()
        y = rect.y()
        lineHeight = 0

        for item in self.itemList:
            wid = item.widget()
            spaceX = self.horizontalSpacing()
            spaceY = self.verticalSpacing()

            nextX = x + item.sizeHint().width() + spaceX
            if nextX - spaceX > rect.right() and lineHeight > 0:
                x = rect.x()
                y = y + lineHeight + spaceY
                nextX = x + item.sizeHint().width() + spaceX
                lineHeight = 0

            if not testOnly:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = nextX
            lineHeight = max(lineHeight, item.sizeHint().height())

        return y + lineHeight - rect.y()

class RecordItemWidget(QWidget):
    doubleClicked = pyqtSignal(str)
    renameRequested = pyqtSignal(str, str)

    def __init__(self, json_filename, display_name, thumb_path, result, parent=None):
        super().__init__(parent)
        self.json_filename = json_filename
        self.display_name = display_name
        self.setFixedSize(260, 180)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(240, 135)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if thumb_path and os.path.exists(thumb_path):
            pixmap = QPixmap(thumb_path)
            if not pixmap.isNull():
                self.thumb_label.setPixmap(pixmap.scaled(240, 135, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
            else:
                self.thumb_label.setText("No Thumbnail")
                self.thumb_label.setStyleSheet("background-color: black; color: white;")
        else:
            self.thumb_label.setText("No Thumbnail")
            self.thumb_label.setStyleSheet("background-color: black; color: white;")
            
        self.name_label = QLabel(display_name)
        self.name_label.setWordWrap(True)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        layout.addWidget(self.thumb_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.name_label)
        
        if result == "win":
            bg_color = "#2E7D32"
            border_color = "#4CAF50"
        elif result == "loss":
            bg_color = "#C62828"
            border_color = "#F44336"
        else:
            bg_color = "#424242"
            border_color = "#757575"
            
        self.setStyleSheet(f"""
            RecordItemWidget {{
                background-color: {bg_color};
                border: 2px solid {border_color};
                border-radius: 8px;
            }}
            RecordItemWidget:hover {{
                border: 2px solid #FFFFFF;
            }}
            QLabel {{
                background: transparent;
                border: none;
                color: white;
            }}
        """)
        
    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit(self.json_filename)
            
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        rename_action = menu.addAction("Rename")
        action = menu.exec(event.globalPos())
        if action == rename_action:
            self.renameRequested.emit(self.json_filename, self.display_name)

class VolumePopup(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setFixedSize(40, 120)
        self.setStyleSheet("""
            VolumePopup { background-color: #222222; border: 1px solid #444444; border-radius: 4px; }
            QSlider { background: transparent; }
            QSlider::groove:vertical { background: #444444; width: 4px; border-radius: 2px; }
            QSlider::handle:vertical { background: #FFFFFF; height: 12px; margin: 0 -4px; border-radius: 6px; }
            QSlider::sub-page:vertical { background: #FF4655; width: 4px; border-radius: 2px; }
            QSlider::add-page:vertical { background: #444444; width: 4px; border-radius: 2px; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        self.slider = QSlider(Qt.Orientation.Vertical)
        self.slider.setRange(0, 100)
        self.slider.setValue(100)
        layout.addWidget(self.slider, alignment=Qt.AlignmentFlag.AlignHCenter)

    def leaveEvent(self, event):
        QTimer.singleShot(100, self._check_hide)
        super().leaveEvent(event)
        
    def _check_hide(self):
        cursor_pos = self.cursor().pos()
        parent_widget = self.parent()
        if parent_widget:
            if not self.geometry().contains(cursor_pos) and not parent_widget.rect().contains(parent_widget.mapFromGlobal(cursor_pos)):
                self.hide()
        else:
            if not self.geometry().contains(cursor_pos):
                self.hide()

class VolumeWidget(QWidget):
    volumeChanged = pyqtSignal(float)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 30)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.icon_label = QLabel("🔊")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("font-size: 16px; color: white;")
        layout.addWidget(self.icon_label)
        
        self.popup = VolumePopup(self)
        self.popup.slider.valueChanged.connect(self._on_value_changed)
        
    def _on_value_changed(self, val):
        self.volumeChanged.emit(val / 100.0)
        if val == 0:
            self.icon_label.setText("🔇")
        elif val < 50:
            self.icon_label.setText("🔉")
        else:
            self.icon_label.setText("🔊")
            
    def enterEvent(self, event):
        pos = self.mapToGlobal(self.rect().topLeft())
        self.popup.move(pos.x() - 5, pos.y() - self.popup.height() + 5)
        self.popup.show()
        super().enterEvent(event)
        
    def leaveEvent(self, event):
        QTimer.singleShot(100, self._check_hide)
        super().leaveEvent(event)

    def _check_hide(self):
        cursor_pos = self.popup.cursor().pos()
        if not self.popup.geometry().contains(cursor_pos) and not self.rect().contains(self.mapFromGlobal(cursor_pos)):
            self.popup.hide()

class TimelineOverlay(QWidget):
    seekRequested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rounds = []
        self.events = []
        self.duration = 0
        self.position = 0
        self.setFixedHeight(45)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.hover_x = -1
        self.is_dragging = False
        
        self.kill_renderer = QSvgRenderer(QByteArray(KILL_SVG))
        self.death_renderer = QSvgRenderer(QByteArray(DEATH_SVG))
        self.assist_renderer = QSvgRenderer(QByteArray(ASSIST_SVG))

    def set_duration(self, duration):
        self.duration = duration
        self.update()

    def set_position(self, position):
        self.position = position
        self.update()

    def set_data(self, rounds, events):
        self.rounds = rounds
        self.events = events
        self.update()

    def leaveEvent(self, event):
        self.hover_x = -1
        self.update()
        super().leaveEvent(event)
        
    def mouseMoveEvent(self, event):
        self.hover_x = event.position().x()
        if self.is_dragging and self.duration > 0:
            x = max(0, min(self.hover_x, self.width()))
            pos_ms = int((x / self.width()) * self.duration)
            self.seekRequested.emit(pos_ms)
        self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if self.duration <= 0 or event.button() != Qt.MouseButton.LeftButton:
            return
        self.is_dragging = True
        x = event.position().x()
        x = max(0, min(x, self.width()))
        pos_ms = int((x / self.width()) * self.duration)
        self.seekRequested.emit(pos_ms)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = False
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        if self.duration <= 0:
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        width = self.width()
        height = self.height()
        
        round_y = height - 12
        round_h = 12
        
        painter.fillRect(0, round_y, width, round_h, QColor("#222222"))
        
        painter.setPen(Qt.PenStyle.NoPen)
        for r in self.rounds:
            x1 = (r['start'] / self.duration) * width
            x2 = (r['end'] / self.duration) * width
            
            phase = r.get('phase')
            if phase == 'InProgress':
                painter.setBrush(QColor("#666666"))
            elif phase == 'PreRound':
                painter.setBrush(QColor("#444444"))
            elif phase == 'PostRound':
                painter.setBrush(QColor("#333333"))
            else:
                painter.setBrush(QColor("#555555"))
                
            painter.drawRect(int(x1), round_y, int(max(1, x2 - x1)), round_h)
            
        progress_w = (self.position / self.duration) * width
        painter.setBrush(QColor(255, 70, 85, 150))
        painter.drawRect(0, round_y, int(progress_w), round_h)
        
        painter.setPen(QPen(QColor("#FF4655"), 2))
        painter.drawLine(int(progress_w), round_y - 2, int(progress_w), round_y + round_h + 2)
            
        painter.setPen(QColor("#888888"))
        for ms in range(0, self.duration, 60000):
            x = (ms / self.duration) * width
            painter.drawLine(int(x), round_y, int(x), round_y + round_h)
            
        for ev in self.events:
            x = (ev['time'] / self.duration) * width
            
            if ev['type'] == 'kill':
                color = QColor("#00FF00")
                renderer = self.kill_renderer
            elif ev['type'] == 'death':
                color = QColor("#FF0000")
                renderer = self.death_renderer
            elif ev['type'] == 'assist':
                color = QColor("#00A2FF")
                renderer = self.assist_renderer
            else:
                color = QColor("#FFFFFF")
                renderer = None
                
            painter.setPen(color)
            painter.drawLine(int(x), round_y - 14, int(x), round_y)
                
            if renderer:
                icon_rect = QRectF(x - 8, round_y - 30, 16, 16)
                renderer.render(painter, icon_rect)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(int(x) - 4, round_y - 18, 8, 8)
            
        if self.hover_x >= 0 and self.hover_x <= width:
            ratio = self.hover_x / width
            hover_ms = int(ratio * self.duration)
            
            s = hover_ms // 1000
            m = s // 60
            s = s % 60
            time_str = f"{m:02d}:{s:02d}"
            
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(int(self.hover_x) - 15, round_y - 25, time_str)