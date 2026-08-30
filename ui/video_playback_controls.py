from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QByteArray, QEvent
from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer

PLAY_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M8,5.14V19.14L19,12.14L8,5.14Z" /></svg>"""
PAUSE_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M14,19H18V5H14M6,19H10V5H6V19Z" /></svg>"""
SKIP_BACK_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M11.5,12L20,18V6M11,18V6L2.5,12L11,18Z" /></svg>"""
SKIP_FORWARD_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M13,6V18L21.5,12M4,18L12.5,12L4,6V18Z" /></svg>"""
PREV_ROUND_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M6,6H8V18H6M9.5,12L18,18V6M16,14.14L12.97,12L16,9.86V14.14Z" /></svg>"""
NEXT_ROUND_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M16,18H18V6H16M8,6L16.5,12L8,18V6Z" /></svg>"""
MINUS_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M19,13H5V11H19V13Z" /></svg>"""
PLUS_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z" /></svg>"""

class PlaybackControlsWidget(QWidget):
    prevRoundRequested = pyqtSignal()
    skipBackRequested = pyqtSignal()
    togglePlayRequested = pyqtSignal()
    skipForwardRequested = pyqtSignal()
    nextRoundRequested = pyqtSignal()
    
    speedDecreaseRequested = pyqtSignal()
    speedIncreaseRequested = pyqtSignal()
    speedToggleRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        layout.addStretch(1)
        
        center_layout = QHBoxLayout()
        center_layout.setSpacing(15)
        
        btn_style = "QPushButton { border-radius: 20px; background-color: #333333; } QPushButton:hover { background-color: #444444; }"
        
        self.prev_round_btn = QPushButton()
        self.prev_round_btn.setFixedSize(40, 40)
        self.prev_round_btn.setStyleSheet(btn_style)
        self.prev_round_btn.setIcon(self._create_icon(PREV_ROUND_SVG))
        self.prev_round_btn.setIconSize(QSize(24, 24))
        self.prev_round_btn.clicked.connect(self.prevRoundRequested.emit)
        
        self.skip_back_btn = QPushButton()
        self.skip_back_btn.setFixedSize(40, 40)
        self.skip_back_btn.setStyleSheet(btn_style)
        self.skip_back_btn.setIcon(self._create_icon(SKIP_BACK_SVG))
        self.skip_back_btn.setIconSize(QSize(24, 24))
        self.skip_back_btn.clicked.connect(self.skipBackRequested.emit)
        
        self.play_pause_btn = QPushButton()
        self.play_pause_btn.setFixedSize(50, 50)
        self.play_pause_btn.setStyleSheet("QPushButton { border-radius: 25px; background-color: #FF4655; } QPushButton:hover { background-color: #FF5865; }")
        self.play_pause_btn.setIcon(self._create_icon(PLAY_SVG))
        self.play_pause_btn.setIconSize(QSize(30, 30))
        self.play_pause_btn.clicked.connect(self.togglePlayRequested.emit)
        
        self.skip_forward_btn = QPushButton()
        self.skip_forward_btn.setFixedSize(40, 40)
        self.skip_forward_btn.setStyleSheet(btn_style)
        self.skip_forward_btn.setIcon(self._create_icon(SKIP_FORWARD_SVG))
        self.skip_forward_btn.setIconSize(QSize(24, 24))
        self.skip_forward_btn.clicked.connect(self.skipForwardRequested.emit)
        
        self.next_round_btn = QPushButton()
        self.next_round_btn.setFixedSize(40, 40)
        self.next_round_btn.setStyleSheet(btn_style)
        self.next_round_btn.setIcon(self._create_icon(NEXT_ROUND_SVG))
        self.next_round_btn.setIconSize(QSize(24, 24))
        self.next_round_btn.clicked.connect(self.nextRoundRequested.emit)
        
        center_layout.addWidget(self.prev_round_btn)
        center_layout.addWidget(self.skip_back_btn)
        center_layout.addWidget(self.play_pause_btn)
        center_layout.addWidget(self.skip_forward_btn)
        center_layout.addWidget(self.next_round_btn)
        
        layout.addLayout(center_layout)
        layout.addStretch(1)
        
        speed_layout = QHBoxLayout()
        speed_layout.setSpacing(5)
        
        speed_btn_style = "QPushButton { border-radius: 15px; background-color: #333333; } QPushButton:hover { background-color: #444444; }"
        
        self.speed_minus_btn = QPushButton()
        self.speed_minus_btn.setFixedSize(30, 30)
        self.speed_minus_btn.setStyleSheet(speed_btn_style)
        self.speed_minus_btn.setIcon(self._create_icon(MINUS_SVG))
        self.speed_minus_btn.setIconSize(QSize(16, 16))
        self.speed_minus_btn.clicked.connect(self.speedDecreaseRequested.emit)
        
        self.speed_label_ui = QLabel("1.0x")
        self.speed_label_ui.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.speed_label_ui.setFixedSize(50, 30)
        self.speed_label_ui.setStyleSheet("QLabel { border-radius: 15px; background-color: transparent; font-size: 16px; font-weight: bold; color: white; margin: 0px; padding: 0px; } QLabel:hover { background-color: rgba(255, 255, 255, 0.1); }")
        self.speed_label_ui.setCursor(Qt.CursorShape.PointingHandCursor)
        self.speed_label_ui.installEventFilter(self)
        
        self.speed_plus_btn = QPushButton()
        self.speed_plus_btn.setFixedSize(30, 30)
        self.speed_plus_btn.setStyleSheet(speed_btn_style)
        self.speed_plus_btn.setIcon(self._create_icon(PLUS_SVG))
        self.speed_plus_btn.setIconSize(QSize(16, 16))
        self.speed_plus_btn.clicked.connect(self.speedIncreaseRequested.emit)
        
        speed_layout.addWidget(self.speed_minus_btn)
        speed_layout.addWidget(self.speed_label_ui)
        speed_layout.addWidget(self.speed_plus_btn)
        
        layout.addLayout(speed_layout)

    def _create_icon(self, svg_bytes, size=24):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer = QSvgRenderer(QByteArray(svg_bytes))
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)

    def eventFilter(self, obj, event):
        if obj == self.speed_label_ui:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self.speedToggleRequested.emit()
                return True
        return super().eventFilter(obj, event)

    def set_playback_state(self, is_playing):
        if is_playing:
            self.play_pause_btn.setIcon(self._create_icon(PAUSE_SVG))
        else:
            self.play_pause_btn.setIcon(self._create_icon(PLAY_SVG))

    def set_speed_label(self, rate, is_bypassed):
        self.speed_label_ui.setText(f"{rate:.1f}x")
        if rate != 1.0 and not is_bypassed:
            self.speed_label_ui.setStyleSheet("QLabel { border-radius: 15px; background-color: rgba(255, 70, 85, 0.5); font-size: 16px; font-weight: bold; color: white; margin: 0px; padding: 0px; } QLabel:hover { background-color: rgba(255, 70, 85, 0.7); }")
        else:
            self.speed_label_ui.setStyleSheet("QLabel { border-radius: 15px; background-color: transparent; font-size: 16px; font-weight: bold; color: white; margin: 0px; padding: 0px; } QLabel:hover { background-color: rgba(255, 255, 255, 0.1); }")
