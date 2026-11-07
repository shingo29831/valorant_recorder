"""Module: @role: タイムライン上のズーム・スクロール座標変換とマウス操作イベント制御を担うウィジェット"""
import time
from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal, QByteArray
from PyQt6.QtGui import QPainter
from PyQt6.QtSvg import QSvgRenderer
from ui.timeline_painter import ASSIST_SVG, DEATH_SVG, KILL_SVG, paint_timeline

class TimelineOverlay(QWidget):
    seekStarted = pyqtSignal()
    seekRequested = pyqtSignal(int)
    seekFinished = pyqtSignal(int)

    clipRangeChanged = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rounds = []
        self.events = []
        self.duration = 0
        self.position = 0
        self.zoom_factor = 1.0
        self.view_start_ms = 0
        self.setFixedHeight(68)
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
        self.last_seek_time = 0

    @property
    def view_duration_ms(self):
        if self.duration <= 0:
            return 0
        return self.duration / self.zoom_factor

    def set_zoom(self, factor, center_ms=None):
        if self.duration <= 0:
            return
        old_view_duration = self.view_duration_ms
        max_zoom = max(1.0, self.duration / 10000.0)
        self.zoom_factor = max(1.0, min(factor, max_zoom))
        new_view_duration = self.view_duration_ms
        
        if center_ms is None:
            center_ms = self.view_start_ms + old_view_duration / 2
            
        self.view_start_ms = center_ms - new_view_duration / 2
        self._clamp_view_start()
        self.update()

    def _clamp_view_start(self):
        max_start = self.duration - self.view_duration_ms
        if self.view_start_ms > max_start:
            self.view_start_ms = max_start
        if self.view_start_ms < 0:
            self.view_start_ms = 0

    def wheelEvent(self, event):
        if self.duration <= 0 or self.zoom_factor <= 1.0:
            return
        delta = event.angleDelta().y()
        scroll_amount = self.view_duration_ms * 0.1
        if delta > 0:
            self.view_start_ms -= scroll_amount
        else:
            self.view_start_ms += scroll_amount
        self._clamp_view_start()
        self.update()
        event.accept()

    def ms_to_x(self, ms):
        if self.view_duration_ms <= 0:
            return 0
        return ((ms - self.view_start_ms) / self.view_duration_ms) * self.width()

    def x_to_ms(self, x):
        if self.view_duration_ms <= 0:
            return 0
        return self.view_start_ms + (x / self.width()) * self.view_duration_ms

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
        self._clamp_view_start()
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
            pos_ms = int(self.x_to_ms(self.hover_x))
            
            if self.dragging_handle == 'start':
                self.clip_start = min(pos_ms, self.clip_end - 1000)
                self.clip_start = max(0, self.clip_start)
                self.clipRangeChanged.emit(self.clip_start, self.clip_end)
                self.update()
                return
            elif self.dragging_handle == 'end':
                self.clip_end = max(pos_ms, self.clip_start + 1000)
                self.clip_end = min(self.duration, self.clip_end)
                self.clipRangeChanged.emit(self.clip_start, self.clip_end)
                self.update()
                return
            else:
                start_x = self.ms_to_x(self.clip_start)
                end_x = self.ms_to_x(self.clip_end)
                if abs(self.hover_x - start_x) <= 5 and 0 <= y <= height:
                    self.hover_handle = 'start'
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                elif abs(self.hover_x - end_x) <= 5 and 0 <= y <= height:
                    self.hover_handle = 'end'
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                else:
                    self.hover_handle = None
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                    
        if not self.edit_mode or (self.edit_mode and not self.hover_handle and not self.dragging_handle):
            if self.is_dragging and self.duration > 0:
                pos_ms = int(self.x_to_ms(self.hover_x))
                pos_ms = max(0, min(pos_ms, self.duration))
                current_time = time.time()
                if current_time - self.last_seek_time > 0.1:
                    self.seekRequested.emit(pos_ms)
                    self.last_seek_time = current_time
            else:
                is_hovering_icon = False
                if self.duration > 0:
                    for ev in self.events:
                        if not self.filters.get(ev['type'], True):
                            continue
                        ev_x = self.ms_to_x(ev['time'])
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
            ev_x = self.ms_to_x(ev['time'])
            if (ev_x - 12) <= x <= (ev_x + 12) and (round_y - 34) <= y <= (round_y - 10):
                clicked_event_time = ev['time']
                break
                
        if clicked_event_time is not None:
            seek_time = max(0, clicked_event_time - 5000)
            self.seekRequested.emit(int(seek_time))
            return

        self.is_dragging = True
        self.seekStarted.emit()
        pos_ms = int(self.x_to_ms(x))
        pos_ms = max(0, min(pos_ms, self.duration))
        self.seekRequested.emit(pos_ms)
        self.last_seek_time = time.time()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.is_dragging and self.duration > 0 and not self.dragging_handle:
                pos_ms = int(self.x_to_ms(event.position().x()))
                pos_ms = max(0, min(pos_ms, self.duration))
                self.seekFinished.emit(pos_ms)
            self.is_dragging = False
            self.dragging_handle = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        if self.duration <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_timeline(self, painter)
