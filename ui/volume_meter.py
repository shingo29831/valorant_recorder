from PyQt6.QtWidgets import QSizePolicy, QWidget
from PyQt6.QtGui import QPainter, QColor, QLinearGradient
from PyQt6.QtCore import Qt, QRectF, QSize

class VolumeMeter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.level = 0.0
        self.gate_threshold = 0.0

    def sizeHint(self) -> QSize:
        return QSize(240, 22)

    def set_level(self, level):
        self.level = min(1.0, max(0.0, level))
        self.update()

    def set_gate_threshold(self, threshold):
        self.gate_threshold = min(1.0, max(0.0, threshold))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        width = self.width()
        height = self.height()

        w_green = int(width * 0.6)
        w_yellow = int(width * 0.85)

        # 背景として各ゾーンを薄い色で描画
        painter.fillRect(0, 0, w_green, height, QColor(0, 255, 0, 40))
        painter.fillRect(w_green, 0, w_yellow - w_green, height, QColor(255, 255, 0, 40))
        painter.fillRect(w_yellow, 0, width - w_yellow, height, QColor(255, 0, 0, 40))

        # 現在のレベルに応じて濃い色を上書き
        if self.level > 0:
            draw_width = int(width * self.level)
            
            # ゲート閾値以下の場合はグレーで描画してカットされていることを示す
            if self.level <= self.gate_threshold and self.gate_threshold > 0:
                painter.fillRect(0, 0, draw_width, height, QColor(150, 150, 150))
            else:
                if draw_width > 0:
                    painter.fillRect(0, 0, min(draw_width, w_green), height, QColor(0, 255, 0))
                if draw_width > w_green:
                    painter.fillRect(w_green, 0, min(draw_width, w_yellow) - w_green, height, QColor(255, 255, 0))
                if draw_width > w_yellow:
                    painter.fillRect(w_yellow, 0, draw_width - w_yellow, height, QColor(255, 0, 0))

        # ゲートの閾値ラインを描画
        if self.gate_threshold > 0:
            gate_x = int(width * self.gate_threshold)
            painter.setPen(QColor(255, 255, 255))
            painter.drawLine(gate_x, 0, gate_x, height)

        painter.setPen(QColor("#3A4652"))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(0, 0, width - 1, height - 1)
