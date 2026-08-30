from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt, pyqtSignal, QByteArray
from PyQt6.QtGui import QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer
from ui.timeline_overlay import KILL_SVG, DEATH_SVG, ASSIST_SVG

class EventToggleIcon(QLabel):
    toggled = pyqtSignal(str, bool)

    def __init__(self, event_type, svg_data, parent=None):
        super().__init__(parent)
        self.event_type = event_type
        self.svg_data = svg_data
        self.is_active = True
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_icon()

    def update_icon(self):
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        
        if not self.is_active:
            painter.setOpacity(0.3)
            
        renderer = QSvgRenderer(QByteArray(self.svg_data))
        renderer.render(painter)
        painter.end()
        self.setPixmap(pixmap)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_active = not self.is_active
            self.update_icon()
            self.toggled.emit(self.event_type, self.is_active)
        super().mousePressEvent(event)

class EventToggleWidget(QWidget):
    filterChanged = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(50)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(15)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.filters = {"kill": True, "death": True, "assist": True}

        self.kill_icon = EventToggleIcon("kill", KILL_SVG)
        self.death_icon = EventToggleIcon("death", DEATH_SVG)
        self.assist_icon = EventToggleIcon("assist", ASSIST_SVG)

        self.kill_icon.toggled.connect(self.on_toggled)
        self.death_icon.toggled.connect(self.on_toggled)
        self.assist_icon.toggled.connect(self.on_toggled)

        layout.addWidget(self.kill_icon)
        layout.addWidget(self.death_icon)
        layout.addWidget(self.assist_icon)

    def on_toggled(self, event_type, is_active):
        self.filters[event_type] = is_active
        self.filterChanged.emit(self.filters)