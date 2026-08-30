from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QByteArray
from PyQt6.QtGui import QColor, QPen, QPainter
from PyQt6.QtSvg import QSvgRenderer

KILL_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  <g fill="#00A2FF" stroke="#000000" stroke-width="0.5">
    <g transform="translate(12,12) rotate(45) translate(-12,-12)">
      <path d="M10.5,17 H13.5 V21 H10.5 Z M7,15 H17 V17 H7 Z M10.5,5 L12,2 L13.5,5 V15 H10.5 Z" />
    </g>
    <g transform="translate(12,12) rotate(-45) translate(-12,-12)">
      <path d="M10.5,17 H13.5 V21 H10.5 Z M7,15 H17 V17 H7 Z M10.5,5 L12,2 L13.5,5 V15 H10.5 Z" />
    </g>
  </g>
</svg>"""

DEATH_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  <g fill="#FF0000" stroke="#000000" stroke-width="0.5">
    <path d="M12,2 C6.5,2 3,6 3,11 c0,3.5 2,6 4,7 h10 c2,-1 4,-3.5 4,-7 C21,6 17.5,2 12,2 z M8,13 c-1.5,0 -2.5,-1.5 -2.5,-3 c0,-1.5 1,-3 2.5,-3 s2.5,1.5 2.5,3 C10.5,11.5 9.5,13 8,13 z M16,13 c-1.5,0 -2.5,-1.5 -2.5,-3 c0,-1.5 1,-3 2.5,-3 s2.5,1.5 2.5,3 C18.5,11.5 17.5,13 16,13 z M12,15.5 l-1.5,-2 h3 L12,15.5 z" />
    <path d="M7.5,19.5 v2.5 h1.5 v-1.5 h2 v1.5 h2 v-1.5 h2 v1.5 h1.5 v-2.5 H7.5 z" />
  </g>
</svg>"""

ASSIST_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  <path d="M21,8h-4.5l1.2-3.6c0.2-0.6-0.3-1.2-0.9-1.2c-0.3,0-0.6,0.1-0.8,0.3L10,9.5V20h8.5c0.8,0,1.5-0.5,1.7-1.2l1.9-6.8 C22.3,11.1,21.8,10.2,21,8z M8,10H4v10h4V10z" fill="#00FF00" stroke="#000000" stroke-width="0.5"/>
</svg>"""

class TimelineOverlay(QWidget):
    seekRequested = pyqtSignal(int)

    clipRangeChanged = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rounds = []
        self.events = []
        self.duration = 0
        self.position = 0
        self.zoom_factor = 1.0
        self.view_start_ms = 0
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
                if abs(self.hover_x - start_x) <= 5 and (round_y - 4) <= y <= (round_y + 12 + 4):
                    self.hover_handle = 'start'
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                elif abs(self.hover_x - end_x) <= 5 and (round_y - 4) <= y <= (round_y + 12 + 4):
                    self.hover_handle = 'end'
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                else:
                    self.hover_handle = None
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                    
        if not self.edit_mode or (self.edit_mode and not self.hover_handle and not self.dragging_handle):
            if self.is_dragging and self.duration > 0:
                pos_ms = int(self.x_to_ms(self.hover_x))
                pos_ms = max(0, min(pos_ms, self.duration))
                self.seekRequested.emit(pos_ms)
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
        pos_ms = int(self.x_to_ms(x))
        pos_ms = max(0, min(pos_ms, self.duration))
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
            x1 = self.ms_to_x(r['start'])
            x2 = self.ms_to_x(r['end'])
            if x2 < 0 or x1 > width:
                continue
                
            phase = r.get('phase')
            if phase == 'InProgress':
                painter.setBrush(QColor("#666666"))
            elif phase == 'PreRound':
                painter.setBrush(QColor("#444444"))
            elif phase == 'PostRound':
                painter.setBrush(QColor("#333333"))
            else:
                painter.setBrush(QColor("#555555"))
                
            draw_x = max(0, x1)
            draw_w = min(width, x2) - draw_x
            if draw_w > 0:
                painter.drawRect(int(draw_x), round_y, int(draw_w), round_h)
            
        progress_x = self.ms_to_x(self.position)
        if progress_x > 0:
            painter.setBrush(QColor(255, 70, 85, 150))
            painter.drawRect(0, round_y, int(min(progress_x, width)), round_h)
        
        if 0 <= progress_x <= width:
            painter.setPen(QPen(QColor("#FF4655"), 2))
            painter.drawLine(int(progress_x), round_y - 2, int(progress_x), round_y + round_h + 2)
            
        intervals = [1000, 5000, 10000, 30000, 60000, 300000]
        target_interval = 60000
        for interval in intervals:
            if self.view_duration_ms / interval <= 20:
                target_interval = interval
                break
                
        painter.setPen(QColor("#888888"))
        start_grid = int(self.view_start_ms // target_interval) * target_interval
        for ms in range(start_grid, int(self.view_start_ms + self.view_duration_ms) + target_interval, target_interval):
            x = self.ms_to_x(ms)
            if 0 <= x <= width:
                painter.drawLine(int(x), round_y, int(x), round_y + round_h)
            
        for ev in self.events:
            if not self.filters.get(ev['type'], True):
                continue
                
            x = self.ms_to_x(ev['time'])
            if x < -20 or x > width + 20:
                continue
                
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
            hover_ms = self.x_to_ms(self.hover_x)
            
            s = int(hover_ms // 1000)
            m = s // 60
            s = s % 60
            time_str = f"{m:02d}:{s:02d}"
            
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(int(self.hover_x) - 15, round_y - 38, time_str)
            
        if self.edit_mode and self.duration > 0:
            start_x = self.ms_to_x(self.clip_start)
            end_x = self.ms_to_x(self.clip_end)
            
            if start_x > 0:
                painter.fillRect(0, round_y, int(min(start_x, width)), round_h, QColor(0, 0, 0, 150))
            if end_x < width:
                painter.fillRect(int(max(0, end_x)), round_y, int(width - max(0, end_x)), round_h, QColor(0, 0, 0, 150))
            
            painter.setPen(QPen(QColor("#00A2FF"), 2))
            draw_start = max(0, start_x)
            draw_end = min(width, end_x)
            if draw_end > draw_start:
                painter.drawRect(int(draw_start), round_y, int(draw_end - draw_start), round_h)
            
            painter.setBrush(QColor("#00A2FF"))
            painter.setPen(Qt.PenStyle.NoPen)
            if 0 <= start_x <= width:
                painter.drawRect(int(start_x) - 2, round_y - 4, 4, round_h + 8)
            if 0 <= end_x <= width:
                painter.drawRect(int(end_x) - 2, round_y - 4, 4, round_h + 8)
