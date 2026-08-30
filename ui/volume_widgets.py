from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSlider
from PyQt6.QtCore import Qt, pyqtSignal, QTimer

class VolumePopup(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setFixedSize(40, 120)
        self.setStyleSheet("""
            VolumePopup { background-color: #222222; border: 1px solid #444444; border-radius: 4px; }
            QSlider { background: transparent; }
            QSlider::groove:vertical { background: #444444; width: 4px; border-radius: 2px; }
            QSlider::handle:vertical { background: #FFFFFF; height: 12px; margin: 0 -4px; border-radius: 6px; }
            QSlider::sub-page:vertical { background: #444444; width: 4px; border-radius: 2px; }
            QSlider::add-page:vertical { background: #FF4655; width: 4px; border-radius: 2px; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        self.slider = QSlider(Qt.Orientation.Vertical)
        self.slider.setRange(0, 200)
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
    volumeChanged = pyqtSignal(int)
    
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
        
        self.previous_volume = 100
        self.is_muted = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_mute()
        super().mousePressEvent(event)
        
    def toggle_mute(self):
        if self.is_muted:
            self.is_muted = False
            self.popup.slider.setValue(self.previous_volume)
        else:
            self.previous_volume = self.popup.slider.value()
            if self.previous_volume == 0:
                self.previous_volume = 100
            self.is_muted = True
            self.popup.slider.setValue(0)
        
    def _on_value_changed(self, val):
        if val > 0:
            self.is_muted = False
            self.previous_volume = val
        elif val == 0 and not self.is_muted:
            self.is_muted = True
            
        self.volumeChanged.emit(val)
        if val == 0:
            self.icon_label.setText("🔇")
        elif val < 50:
            self.icon_label.setText("🔉")
        else:
            self.icon_label.setText("🔊")
            
    def set_volume(self, volume):
        val = int(volume)
        self.popup.slider.blockSignals(True)
        self.popup.slider.setValue(val)
        self.popup.slider.blockSignals(False)
        if val == 0:
            self.is_muted = True
            self.icon_label.setText("🔇")
        elif val < 50:
            self.is_muted = False
            self.previous_volume = val
            self.icon_label.setText("🔉")
        else:
            self.is_muted = False
            self.previous_volume = val
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

class MicVolumePopup(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setFixedSize(40, 120)
        self.setStyleSheet("""
            MicVolumePopup { background-color: #222222; border: 1px solid #444444; border-radius: 4px; }
            QSlider { background: transparent; }
            QSlider::groove:vertical { background: #444444; width: 4px; border-radius: 2px; }
            QSlider::handle:vertical { background: #FFFFFF; height: 12px; margin: 0 -4px; border-radius: 6px; }
            QSlider::sub-page:vertical { background: #444444; width: 4px; border-radius: 2px; }
            QSlider::add-page:vertical { background: #00A2FF; width: 4px; border-radius: 2px; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        self.slider = QSlider(Qt.Orientation.Vertical)
        self.slider.setRange(0, 200)
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

class MicVolumeWidget(QWidget):
    volumeChanged = pyqtSignal(int)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 30)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.icon_label = QLabel("🎤")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("font-size: 16px; color: white;")
        layout.addWidget(self.icon_label)
        
        self.popup = MicVolumePopup(self)
        self.popup.slider.valueChanged.connect(self._on_value_changed)
        
        self.previous_volume = 100
        self.is_muted = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_mute()
        super().mousePressEvent(event)
        
    def toggle_mute(self):
        if self.is_muted:
            self.is_muted = False
            self.popup.slider.setValue(self.previous_volume)
        else:
            self.previous_volume = self.popup.slider.value()
            if self.previous_volume == 0:
                self.previous_volume = 100
            self.is_muted = True
            self.popup.slider.setValue(0)
        
    def _on_value_changed(self, val):
        if val > 0:
            self.is_muted = False
            self.previous_volume = val
        elif val == 0 and not self.is_muted:
            self.is_muted = True
            
        self.volumeChanged.emit(val)
        if val == 0:
            self.icon_label.setStyleSheet("font-size: 16px; color: gray;")
        else:
            self.icon_label.setStyleSheet("font-size: 16px; color: white;")
            
    def set_volume(self, volume):
        val = int(volume)
        self.popup.slider.blockSignals(True)
        self.popup.slider.setValue(val)
        self.popup.slider.blockSignals(False)
        if val == 0:
            self.is_muted = True
            self.icon_label.setStyleSheet("font-size: 16px; color: gray;")
        else:
            self.is_muted = False
            self.previous_volume = val
            self.icon_label.setStyleSheet("font-size: 16px; color: white;")
            
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
