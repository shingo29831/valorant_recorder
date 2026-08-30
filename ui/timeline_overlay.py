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
