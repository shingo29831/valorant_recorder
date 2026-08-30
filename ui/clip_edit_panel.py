from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel
from PyQt6.QtCore import pyqtSignal

class ClipEditPanel(QWidget):
    setStartRequested = pyqtSignal()
    setEndRequested = pyqtSignal()
    generateRequested = pyqtSignal()
    cancelRequested = pyqtSignal()

    def __init__(self, t, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: #222222; border-radius: 5px;")
        layout = QHBoxLayout(self)
        
        self.start_btn = QPushButton(t.set_start)
        self.start_btn.clicked.connect(self.setStartRequested.emit)
        self.start_label = QLabel("00:00")
        
        self.end_btn = QPushButton(t.set_end)
        self.end_btn.clicked.connect(self.setEndRequested.emit)
        self.end_label = QLabel("00:00")
        
        self.generate_btn = QPushButton(t.generate)
        self.generate_btn.setStyleSheet("background-color: #FF4655; font-weight: bold;")
        self.generate_btn.clicked.connect(self.generateRequested.emit)
        
        self.cancel_edit_btn = QPushButton(t.cancel)
        self.cancel_edit_btn.clicked.connect(self.cancelRequested.emit)
        
        layout.addWidget(self.start_btn)
        layout.addWidget(self.start_label)
        layout.addSpacing(20)
        layout.addWidget(self.end_btn)
        layout.addWidget(self.end_label)
        layout.addStretch()
        layout.addWidget(self.cancel_edit_btn)
        layout.addWidget(self.generate_btn)

    def update_labels(self, start_text, end_text):
        self.start_label.setText(start_text)
        self.end_label.setText(end_text)

    def set_generate_enabled(self, enabled, text):
        self.generate_btn.setEnabled(enabled)
        self.generate_btn.setText(text)
