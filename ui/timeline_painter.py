"""Module: @role: タイムラインのSVGアイコン定義およびラウンド・イベント・クリップ範囲の描画処理を担うモジュール"""
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

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


def paint_timeline(widget: QWidget, painter: QPainter) -> None:
    width = widget.width()
    height = widget.height()

    round_y = height - 12
    round_h = 12

    painter.fillRect(0, round_y, width, round_h, QColor("#222222"))

    painter.setPen(Qt.PenStyle.NoPen)
    for r in widget.rounds:
        x1 = widget.ms_to_x(r["start"])
        x2 = widget.ms_to_x(r["end"])
        if x2 < 0 or x1 > width:
            continue

        phase = r.get("phase")
        if phase == "InProgress":
            painter.setBrush(QColor("#666666"))
        elif phase == "PreRound":
            painter.setBrush(QColor("#444444"))
        elif phase == "PostRound":
            painter.setBrush(QColor("#333333"))
        else:
            painter.setBrush(QColor("#555555"))

        draw_x = max(0, x1)
        draw_w = min(width, x2) - draw_x
        if draw_w > 0:
            painter.drawRect(int(draw_x), round_y, int(draw_w), round_h)

    progress_x = widget.ms_to_x(widget.position)
    if progress_x > 0:
        painter.setBrush(QColor(255, 70, 85, 150))
        painter.drawRect(0, round_y, int(min(progress_x, width)), round_h)

    if 0 <= progress_x <= width:
        painter.setPen(QPen(QColor("#FF4655"), 2))
        painter.drawLine(int(progress_x), round_y - 2, int(progress_x), round_y + round_h + 2)

    intervals = [1000, 5000, 10000, 30000, 60000, 300000]
    target_interval = 60000
    for interval in intervals:
        if widget.view_duration_ms / interval <= 20:
            target_interval = interval
            break

    painter.setPen(QColor("#888888"))
    start_grid = int(widget.view_start_ms // target_interval) * target_interval
    for ms in range(start_grid, int(widget.view_start_ms + widget.view_duration_ms) + target_interval, target_interval):
        x = widget.ms_to_x(ms)
        if 0 <= x <= width:
            painter.drawLine(int(x), round_y, int(x), round_y + round_h)

    if widget.edit_mode and widget.duration > 0:
        start_x = widget.ms_to_x(widget.clip_start)
        end_x = widget.ms_to_x(widget.clip_end)

        if start_x > 0:
            painter.fillRect(0, 0, int(min(start_x, width)), height, QColor(0, 0, 0, 150))
        if end_x < width:
            painter.fillRect(int(max(0, end_x)), 0, int(width - max(0, end_x)), height, QColor(0, 0, 0, 150))

        painter.setPen(QPen(QColor("#00A2FF"), 2))
        draw_start = max(0, start_x)
        draw_end = min(width, end_x)
        if draw_end > draw_start:
            painter.drawRect(int(draw_start), round_y, int(draw_end - draw_start), round_h)

        painter.setBrush(QColor("#00A2FF"))
        painter.setPen(Qt.PenStyle.NoPen)
        if 0 <= start_x <= width:
            painter.drawRect(int(start_x) - 2, 0, 4, height)
        if 0 <= end_x <= width:
            painter.drawRect(int(end_x) - 2, 0, 4, height)

    for ev in widget.events:
        if not widget.filters.get(ev["type"], True):
            continue

        x = widget.ms_to_x(ev["time"])
        if x < -20 or x > width + 20:
            continue

        if ev["type"] == "kill":
            color = QColor("#00A2FF")
            renderer = widget.kill_renderer
        elif ev["type"] == "death":
            color = QColor("#FF0000")
            renderer = widget.death_renderer
        elif ev["type"] == "assist":
            color = QColor("#00FF00")
            renderer = widget.assist_renderer
        elif ev["type"] == "ult":
            color = QColor("#FF00FF")
            renderer = None
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

    if 0 <= widget.hover_x <= width:
        hover_ms = widget.x_to_ms(widget.hover_x)
        s = int(hover_ms // 1000)
        m = s // 60
        s = s % 60
        time_str = f"{m:02d}:{s:02d}"

        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(int(widget.hover_x) - 15, round_y - 38, time_str)
