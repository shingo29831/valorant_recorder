import os
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QMenu, QPushButton
from PyQt6.QtCore import Qt, pyqtSignal, QByteArray, QTimer
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen
from PyQt6.QtSvg import QSvgRenderer

STAR_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="gold" stroke="black" stroke-width="1">
  <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
</svg>"""

class LoadingSpinner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rotate)
        self.timer.start(50)
        self.setFixedSize(40, 40)

    def rotate(self):
        self.angle = (self.angle + 30) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(self.angle)
        
        pen = QPen(QColor("white"))
        pen.setWidth(4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(-12, -12, 24, 24, 0, 270 * 16)

class RecordItemWidget(QWidget):
    doubleClicked = pyqtSignal(str)
    renameRequested = pyqtSignal(str, str)
    deleteRequested = pyqtSignal(str)

    def __init__(self, json_filename, display_name, thumb_path, result, is_favorite=False, mmr_change=0, party_members=None, is_fetching_api=False, parent=None):
        super().__init__(parent)
        self.json_filename = json_filename
        self.display_name = display_name
        self.is_favorite = is_favorite
        self.is_fetching_api = is_fetching_api
        self.thumb_path = thumb_path
        self.setFixedSize(260, 210)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(240, 135)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        if self.thumb_path and os.path.exists(self.thumb_path):
            self._load_thumbnail()
        else:
            self.thumb_label.setText("No Thumbnail")
            self.thumb_label.setStyleSheet("background-color: black; color: white;")
            if self.thumb_path:
                self.thumb_timer = QTimer(self)
                self.thumb_timer.timeout.connect(self._check_thumbnail)
                self.thumb_timer.start(1000)
            
        if self.is_fetching_api:
            self.overlay = QLabel(self.thumb_label)
            self.overlay.setFixedSize(240, 135)
            self.overlay.setStyleSheet("background-color: rgba(0, 0, 0, 150);")
            
            self.spinner = LoadingSpinner(self.overlay)
            self.spinner.move((240 - 40) // 2, (135 - 40) // 2)
            
        self.fav_icon = QLabel(self.thumb_label)
        self.fav_icon.setFixedSize(24, 24)
        fav_pixmap = QPixmap(24, 24)
        fav_pixmap.fill(Qt.GlobalColor.transparent)
        fav_painter = QPainter(fav_pixmap)
        fav_renderer = QSvgRenderer(QByteArray(STAR_SVG))
        fav_renderer.render(fav_painter)
        fav_painter.end()
        self.fav_icon.setPixmap(fav_pixmap)
        self.fav_icon.move(5, 5)
        self.fav_icon.setVisible(self.is_favorite)

        self.name_label = QLabel(display_name)
        self.name_label.setWordWrap(True)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        layout.addWidget(self.thumb_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.name_label)
        
        if mmr_change != 0:
            mmr_text = f"昇格/降格: +{mmr_change}" if mmr_change > 0 else f"昇格/降格: {mmr_change}"
            mmr_color = "#00FF00" if mmr_change > 0 else "#FF4655"
            self.mmr_label = QLabel(mmr_text)
            self.mmr_label.setStyleSheet(f"color: {mmr_color}; font-weight: bold; font-size: 12px;")
            self.mmr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self.mmr_label)

        if party_members and len(party_members) > 1:
            party_text = f"Party: {len(party_members)}人"
            self.party_label = QLabel(party_text)
            self.party_label.setStyleSheet("color: #888888; font-size: 11px;")
            self.party_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.party_label.setToolTip("\n".join(party_members))
            layout.addWidget(self.party_label)
        
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
        
        self.checkbox_mode = "delete"
        self.checkbox = QPushButton(self)
        self.checkbox.setCheckable(True)
        self.checkbox.setFixedSize(24, 24)
        self.checkbox.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 0.6);
                border: 2px solid #FFFFFF;
                border-radius: 4px;
            }
        """)
        self.checkbox.setText("")
        self.checkbox.toggled.connect(self._on_checkbox_toggled)
        self.checkbox.move(self.width() - 29, 5)
        self.checkbox.hide()

    def _load_thumbnail(self):
        pixmap = QPixmap(self.thumb_path)
        if not pixmap.isNull():
            self.thumb_label.setPixmap(pixmap.scaled(240, 135, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
            self.thumb_label.setStyleSheet("")
        else:
            self.thumb_label.setText("No Thumbnail")
            self.thumb_label.setStyleSheet("background-color: black; color: white;")

    def _check_thumbnail(self):
        if self.thumb_path and os.path.exists(self.thumb_path):
            self._load_thumbnail()
            self.thumb_timer.stop()

    def _on_checkbox_toggled(self, checked):
        if checked:
            self.checkbox.setText("✓")
            color = "#FF4655" if self.checkbox_mode == "delete" else "#FFD700"
            text_color = "white" if self.checkbox_mode == "delete" else "black"
            self.checkbox.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color};
                    color: {text_color};
                    font-weight: bold;
                    border: 2px solid #FFFFFF;
                    border-radius: 4px;
                }}
            """)
        else:
            self.checkbox.setText("")
            self.checkbox.setStyleSheet("""
                QPushButton {
                    background-color: rgba(0, 0, 0, 0.6);
                    border: 2px solid #FFFFFF;
                    border-radius: 4px;
                }
            """)

    def set_selection_mode(self, enabled, mode="delete"):
        self.checkbox_mode = mode
        self.checkbox.setVisible(enabled)
        if not enabled:
            self.checkbox.setChecked(False)
        else:
            self._on_checkbox_toggled(self.checkbox.isChecked())

    def set_checked(self, checked):
        self.checkbox.setChecked(checked)

    def is_checked(self):
        return self.checkbox.isChecked()
        
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.checkbox.isVisible():
                self.checkbox.setChecked(not self.checkbox.isChecked())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit(self.json_filename)
            
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        rename_action = menu.addAction("Rename")
        delete_action = menu.addAction("Delete")
        action = menu.exec(event.globalPos())
        if action == rename_action:
            self.renameRequested.emit(self.json_filename, self.display_name)
        elif action == delete_action:
            self.deleteRequested.emit(self.json_filename)
