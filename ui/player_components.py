import os
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QLayout, QMenu, QPushButton
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRect, QSize, QByteArray
from PyQt6.QtGui import QPixmap, QPainter
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtSvg import QSvgRenderer

STAR_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="gold" stroke="black" stroke-width="1">
  <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
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
    deleteRequested = pyqtSignal(str)

    def __init__(self, json_filename, display_name, thumb_path, result, is_favorite=False, parent=None):
        super().__init__(parent)
        self.json_filename = json_filename
        self.display_name = display_name
        self.is_favorite = is_favorite
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

class TimelineOverlay(QWidget):
    seekRequested = pyqtSignal(int)

    clipRangeChanged = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rounds = []
        self.events = []
        self.duration = 0
        self.position = 0
        self.setFixedHeight(55)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.hover_x = -1
        self.is_dragging = False
        self.filters = {"kill": True, "death": True, "assist": True}
        
        self.kill_renderer = QSvgRenderer(QByteArray(KILL_SVG))
        self.death_renderer = QSvgRenderer(QByteArray(DEATH_SVG))
        self.assist_renderer = QSvgRenderer(QByteArray(ASSIST_SVG))
        
        self.edit_mode = False
        self.clip_start = 0
        self.clip_end = 0
        self.dragging_handle = None
        self.hover_handle = None

    def set_edit_mode(self, enabled, start=0, end=0):
        self.edit_mode = enabled
        self.clip_start = start
        self.clip_end = end
        self.update()

    def set_clip_range(self, start, end):
        self.clip_start = start
        self.clip_end = end
        self.update()

    def set_filters(self, filters):
        self.filters = filters
        self.update()

    def set_duration(self, duration):
        self.duration = duration
        self.update()

    def set_position(self, position):
        self.position = position
        self.update()

    def set_data(self, rounds, events):
        self.rounds = rounds
        priority = {'assist': 0, 'death': 1, 'kill': 2}
        self.events = sorted(events, key=lambda e: (priority.get(e['type'], -1), e['time']))
        self.update()

    def leaveEvent(self, event):
        self.hover_x = -1
        self.update()
        super().leaveEvent(event)
        
    def mouseMoveEvent(self, event):
        self.hover_x = event.position().x()
        y = event.position().y()
        width = self.width()
        height = self.height()
        round_y = height - 12
        
        if self.edit_mode and self.duration > 0:
            x = max(0, min(self.hover_x, width))
            pos_ms = int((x / width) * self.duration)
            
            if self.dragging_handle == 'start':
                self.clip_start = min(pos_ms, self.clip_end - 1000)
                self.clipRangeChanged.emit(self.clip_start, self.clip_end)
                self.update()
                return
            elif self.dragging_handle == 'end':
                self.clip_end = max(pos_ms, self.clip_start + 1000)
                self.clipRangeChanged.emit(self.clip_start, self.clip_end)
                self.update()
                return
            else:
                start_x = (self.clip_start / self.duration) * width
                end_x = (self.clip_end / self.duration) * width
                if abs(x - start_x) <= 5 and (round_y - 4) <= y <= (round_y + 12 + 4):
                    self.hover_handle = 'start'
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                elif abs(x - end_x) <= 5 and (round_y - 4) <= y <= (round_y + 12 + 4):
                    self.hover_handle = 'end'
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                else:
                    self.hover_handle = None
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                    
        if not self.edit_mode or (self.edit_mode and not self.hover_handle and not self.dragging_handle):
            if self.is_dragging and self.duration > 0:
                x = max(0, min(self.hover_x, self.width()))
                pos_ms = int((x / self.width()) * self.duration)
                self.seekRequested.emit(pos_ms)
            else:
                is_hovering_icon = False
                if self.duration > 0:
                    for ev in self.events:
                        if not self.filters.get(ev['type'], True):
                            continue
                        ev_x = (ev['time'] / self.duration) * width
                        if (ev_x - 12) <= self.hover_x <= (ev_x + 12) and (round_y - 34) <= y <= (round_y - 10):
                            is_hovering_icon = True
                            break
                if is_hovering_icon:
                    self.setCursor(Qt.CursorShape.PointingHandCursor)
                elif not getattr(self, 'hover_handle', None):
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                
        self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if self.duration <= 0 or event.button() != Qt.MouseButton.LeftButton:
            return
            
        if self.edit_mode and self.hover_handle:
            self.dragging_handle = self.hover_handle
            return
            
        x = event.position().x()
        y = event.position().y()
        width = self.width()
        height = self.height()
        round_y = height - 12
        
        clicked_event_time = None
        for ev in reversed(self.events):
            if not self.filters.get(ev['type'], True):
                continue
            ev_x = (ev['time'] / self.duration) * width
            if (ev_x - 12) <= x <= (ev_x + 12) and (round_y - 34) <= y <= (round_y - 10):
                clicked_event_time = ev['time']
                break
                
        if clicked_event_time is not None:
            seek_time = max(0, clicked_event_time - 5000)
            self.seekRequested.emit(seek_time)
            return

        self.is_dragging = True
        x = max(0, min(x, self.width()))
        pos_ms = int((x / self.width()) * self.duration)
        self.seekRequested.emit(pos_ms)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = False
            self.dragging_handle = None
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
            if not self.filters.get(ev['type'], True):
                continue
                
            x = (ev['time'] / self.duration) * width
            
            if ev['type'] == 'kill':
                color = QColor("#00A2FF")
                renderer = self.kill_renderer
            elif ev['type'] == 'death':
                color = QColor("#FF0000")
                renderer = self.death_renderer
            elif ev['type'] == 'assist':
                color = QColor("#00FF00")
                renderer = self.assist_renderer
            else:
                color = QColor("#FFFFFF")
                renderer = None
                
            painter.setPen(color)
            painter.drawLine(int(x), round_y - 10, int(x), round_y)
                
            if renderer:
                icon_rect = QRectF(x - 12, round_y - 34, 24, 24)
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
            painter.drawText(int(self.hover_x) - 15, round_y - 38, time_str)
            
        if self.edit_mode and self.duration > 0:
            start_x = (self.clip_start / self.duration) * width
            end_x = (self.clip_end / self.duration) * width
            
            painter.fillRect(0, round_y, int(start_x), round_h, QColor(0, 0, 0, 150))
            painter.fillRect(int(end_x), round_y, int(width - end_x), round_h, QColor(0, 0, 0, 150))
            
            painter.setPen(QPen(QColor("#00A2FF"), 2))
            painter.drawRect(int(start_x), round_y, int(end_x - start_x), round_h)
            
            painter.setBrush(QColor("#00A2FF"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(int(start_x) - 2, round_y - 4, 4, round_h + 8)
            painter.drawRect(int(end_x) - 2, round_y - 4, 4, round_h + 8)

class MicVolumePopup(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setFixedSize(40, 120)
        self.setStyleSheet("""
            MicVolumePopup { background-color: #222222; border: 1px solid #444444; border-radius: 4px; }
            QSlider { background: transparent; }
            QSlider::groove:vertical { background: #444444; width: 4px; border-radius: 2px; }
            QSlider::handle:vertical { background: #FFFFFF; height: 12px; margin: 0 -4px; border-radius: 6px; }
            QSlider::sub-page:vertical { background: #00A2FF; width: 4px; border-radius: 2px; }
            QSlider::add-page:vertical { background: #444444; width: 4px; border-radius: 2px; }
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