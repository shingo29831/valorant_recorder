"""Module: @role: 再生画面上部にホバー連動でスライド出現し、他録画へクイック遷移する横並び動画一覧バーを提供するモジュール"""

import os
from enum import Enum, auto
from typing import Optional, List, Dict, Any
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel, QPushButton
)
from PyQt6.QtCore import (
    Qt, QPoint, QSize, QByteArray, QPropertyAnimation, QVariantAnimation,
    QEasingCurve, QTimer, pyqtSignal, pyqtProperty
)
from PyQt6.QtGui import QPixmap, QPixmapCache, QFontMetrics, QWheelEvent, QCursor, QIcon, QPainter
from PyQt6.QtSvg import QSvgRenderer
from core.config import Config
from ui.record_data_loader import RecordDataLoader


CHEVRON_LEFT_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z"/>
</svg>"""

CHEVRON_RIGHT_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z"/>
</svg>"""


def _create_svg_icon(svg_bytes: bytes, size: int = 20) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class SwitcherState(Enum):
    HIDDEN = auto()
    EXPANDED = auto()


class SwitcherItemCard(QWidget):
    clicked = pyqtSignal(str)

    def __init__(self, record: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.filename = record.get("filename", "")
        self.thumb_path = record.get("thumb_path", "")
        self.display_name = record.get("display_name", "")
        self.result = record.get("result", "unknown")
        
        self.setFixedSize(140, 100)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        
        self.thumb_label = QLabel()
        self.thumb_label.setObjectName("thumbLabel")
        self.thumb_label.setFixedSize(132, 74)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setScaledContents(True)

        self._thumb_retries = 0
        if not self._load_thumbnail():
            self.thumb_label.setText("No Image")
            self.thumb_label.setStyleSheet("background-color: #111111; color: #888888; font-size: 10px; border-radius: 4px;")
            if self.thumb_path:
                self.thumb_timer = QTimer(self)
                self.thumb_timer.timeout.connect(self._check_thumbnail)
                self.thumb_timer.start(800)
        
        self.title_label = QLabel()
        self.title_label.setStyleSheet("color: #FFFFFF; font-size: 11px; font-weight: bold; background: transparent;")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        fm = QFontMetrics(self.title_label.font())
        elided = fm.elidedText(self.display_name, Qt.TextElideMode.ElideMiddle, 132)
        self.title_label.setText(elided)
        self.title_label.setToolTip(self.display_name)
        
        layout.addWidget(self.thumb_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.title_label)
        
        border_color = "#4CAF50" if self.result == "win" else ("#FF4655" if self.result == "loss" else "#757575")
        self.setStyleSheet(f"""
            SwitcherItemCard {{
                background-color: rgba(20, 30, 42, 0.92);
                border: 2px solid {border_color};
                border-radius: 6px;
            }}
            SwitcherItemCard:hover {{
                background-color: rgba(40, 60, 85, 0.98);
                border: 2px solid #FFFFFF;
            }}
            QLabel#thumbLabel {{
                background-color: transparent;
                border-radius: 4px;
            }}
        """)

    def _load_thumbnail(self) -> bool:
        if not self.thumb_path or not os.path.exists(self.thumb_path):
            return False

        cache_key = f"switcher_thumb_{self.thumb_path}"
        cached = QPixmapCache.find(cache_key)
        if cached is not None and not cached.isNull():
            self.thumb_label.setText("")
            self.thumb_label.setStyleSheet("background-color: transparent; border-radius: 4px;")
            self.thumb_label.setPixmap(cached)
            return True

        pixmap = QPixmap(self.thumb_path)
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                132, 74,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            if scaled.width() > 132 or scaled.height() > 74:
                x = max(0, (scaled.width() - 132) // 2)
                y = max(0, (scaled.height() - 74) // 2)
                scaled = scaled.copy(x, y, 132, 74)

            QPixmapCache.insert(cache_key, scaled)
            self.thumb_label.setText("")
            self.thumb_label.setStyleSheet("background-color: transparent; border-radius: 4px;")
            self.thumb_label.setPixmap(scaled)
            return True
        return False

    def _check_thumbnail(self) -> None:
        self._thumb_retries += 1
        if self._load_thumbnail():
            if hasattr(self, 'thumb_timer') and self.thumb_timer.isActive():
                self.thumb_timer.stop()
        elif self._thumb_retries >= 15:
            if hasattr(self, 'thumb_timer') and self.thumb_timer.isActive():
                self.thumb_timer.stop()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            filename = self.filename
            event.accept()
            # イベント処理中のウィジェット自己破壊によるAccess Violationを防止
            QTimer.singleShot(0, lambda: self.clicked.emit(filename))
            return
        super().mousePressEvent(event)


class VideoTopSwitcher(QWidget):
    recordSelected = pyqtSignal(str)

    BAR_HEIGHT = 145
    TRIGGER_ZONE_Y = 65
    CARD_WIDTH = 140
    CARD_HEIGHT = 100
    CARD_GAP = 8
    VISIBLE_CARD_COUNT = 5
    SCROLL_AREA_WIDTH = (CARD_WIDTH * VISIBLE_CARD_COUNT) + (CARD_GAP * (VISIBLE_CARD_COUNT - 1))
    SLIDE_STEP = (CARD_WIDTH + CARD_GAP) * 4

    def __init__(self, config: Config, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        self.state = SwitcherState.HIDDEN
        self.current_filename: Optional[str] = None
        self._all_records: List[Dict[str, Any]] = []

        self.setFixedHeight(self.BAR_HEIGHT)
        self.setMouseTracking(True)
        self.setStyleSheet("""
            VideoTopSwitcher {
                background-color: rgba(15, 25, 35, 0.96);
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
                border: 1px solid #FF4655;
                border-top: none;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 6, 10, 0)
        main_layout.setSpacing(2)

        carousel_container = QWidget()
        carousel_container.setStyleSheet("background: transparent;")
        carousel_layout = QHBoxLayout(carousel_container)
        carousel_layout.setContentsMargins(0, 0, 0, 0)
        carousel_layout.setSpacing(8)
        carousel_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_left = QPushButton()
        self.btn_left.setFixedSize(32, self.CARD_HEIGHT)
        self.btn_left.setIcon(_create_svg_icon(CHEVRON_LEFT_SVG, 20))
        self.btn_left.setIconSize(QSize(20, 20))
        self.btn_left.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_left.setToolTip("前の動画へスライド")
        self.btn_left.clicked.connect(self._slide_left)

        self.scroll_area = QScrollArea()
        self.scroll_area.setFixedSize(self.SCROLL_AREA_WIDTH, 106)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self.btn_right = QPushButton()
        self.btn_right.setFixedSize(32, self.CARD_HEIGHT)
        self.btn_right.setIcon(_create_svg_icon(CHEVRON_RIGHT_SVG, 20))
        self.btn_right.setIconSize(QSize(20, 20))
        self.btn_right.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_right.setToolTip("次の動画へスライド")
        self.btn_right.clicked.connect(self._slide_right)

        btn_style = """
            QPushButton {
                background-color: rgba(20, 30, 42, 0.9);
                border: 1px solid rgba(255, 70, 85, 0.6);
                border-radius: 6px;
            }
            QPushButton:hover:!disabled {
                background-color: #FF4655;
                border: 1px solid #FFFFFF;
            }
            QPushButton:pressed:!disabled {
                background-color: #CC3744;
            }
            QPushButton:disabled {
                background-color: rgba(20, 30, 42, 0.35);
                border: 1px solid rgba(80, 80, 80, 0.25);
            }
        """
        self.btn_left.setStyleSheet(btn_style)
        self.btn_right.setStyleSheet(btn_style)

        carousel_layout.addStretch()
        carousel_layout.addWidget(self.btn_left)
        carousel_layout.addWidget(self.scroll_area)
        carousel_layout.addWidget(self.btn_right)
        carousel_layout.addStretch()

        self.cards_container = QWidget()
        self.cards_container.setStyleSheet("background: transparent;")
        self.cards_layout = QHBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(self.CARD_GAP)
        self.scroll_area.setWidget(self.cards_container)

        self.empty_label = QLabel("他の録画が見つかりません")
        self.empty_label.setStyleSheet("color: #888888; font-size: 12px; background: transparent;")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.hide()
        self.cards_layout.addWidget(self.empty_label)
        self.cards_layout.addStretch()

        self.handle_bar = QLabel("▼ ほかの録画 (クリックで切替) ▼")
        self.handle_bar.setFixedHeight(24)
        self.handle_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.handle_bar.setStyleSheet("""
            QLabel {
                color: #FFFFFF;
                font-size: 11px;
                font-weight: bold;
                background-color: rgba(255, 70, 85, 0.85);
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QLabel:hover {
                background-color: #FF4655;
            }
        """)
        self.handle_bar.setCursor(Qt.CursorShape.PointingHandCursor)

        main_layout.addWidget(carousel_container)
        main_layout.addWidget(self.handle_bar)

        self._offset = 0
        self.anim = QPropertyAnimation(self, b"offset", self)
        self.anim.setDuration(320)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.scroll_anim = QVariantAnimation(self)
        self.scroll_anim.setDuration(260)
        self.scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.scroll_anim.valueChanged.connect(self._on_scroll_anim_step)
        self.scroll_anim.finished.connect(self._update_arrow_buttons)

        bar = self.scroll_area.horizontalScrollBar()
        bar.valueChanged.connect(self._update_arrow_buttons)
        bar.rangeChanged.connect(self._update_arrow_buttons)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(300)
        self.hide_timer.timeout.connect(self._check_and_hide)

        # 動画ウィジェットのマウスイベント遮断対策として定期座標監視
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(60)
        self.poll_timer.timeout.connect(self._poll_mouse_position)
        self.poll_timer.start()

        self.show()
        self.raise_()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y() or event.angleDelta().x()
        bar = self.scroll_area.horizontalScrollBar()
        bar.setValue(bar.value() - delta)
        event.accept()

    offsetChanged = pyqtSignal(int)

    @pyqtProperty(int)
    def offset(self) -> int:
        return self._offset

    @offset.setter
    def offset(self, val: int) -> None:
        self._offset = val
        self.move(0, val - self.BAR_HEIGHT)
        self.offsetChanged.emit(val)

    def update_geometry(self, parent_width: int) -> None:
        self.setFixedWidth(parent_width)
        target = self._get_target_offset(self.state)
        self.offset = target

    def _get_target_offset(self, state: SwitcherState) -> int:
        if state == SwitcherState.HIDDEN:
            return 0
        return self.BAR_HEIGHT

    def set_state(self, new_state: SwitcherState, immediate: bool = False) -> None:
        if self.state == new_state and not immediate:
            return
        self.state = new_state
        target = self._get_target_offset(new_state)
        self.anim.stop()
        if immediate:
            self.offset = target
        else:
            # 出現時は滑らかに減速、収納時は自然に加速して戻るイージングを設定
            curve = QEasingCurve.Type.OutCubic if new_state == SwitcherState.EXPANDED else QEasingCurve.Type.InCubic
            self.anim.setEasingCurve(curve)
            self.anim.setStartValue(self._offset)
            self.anim.setEndValue(target)
            self.anim.start()

    def _poll_mouse_position(self) -> None:
        parent = self.parentWidget()
        if not parent or not parent.isVisible():
            return

        parent_pos = parent.mapFromGlobal(QCursor.pos())
        if not parent.rect().contains(parent_pos):
            if self.state != SwitcherState.HIDDEN:
                self._start_hide_timer()
            return

        x = parent_pos.x()
        y = parent_pos.y()
        if self.state == SwitcherState.HIDDEN:
            # 左右端の操作ボタン（戻る・編集等）操作時はスイッチャーを展開しない
            if 0 <= y <= self.TRIGGER_ZONE_Y and 75 <= x <= parent.width() - 75:
                self.hide_timer.stop()
                self.set_state(SwitcherState.EXPANDED)
        elif self.state == SwitcherState.EXPANDED:
            # 展開後は画面下半分地点に達するまで戻さない
            dismiss_threshold_y = parent.height() / 2
            if y >= dismiss_threshold_y or y < 0:
                self._start_hide_timer()
            else:
                self.hide_timer.stop()

    def handle_parent_mouse_move(self, local_pos: QPoint) -> None:
        self._poll_mouse_position()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.hide_timer.stop()
        if self.state != SwitcherState.EXPANDED:
            self.set_state(SwitcherState.EXPANDED)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        parent = self.parentWidget()
        if parent:
            parent_pos = parent.mapFromGlobal(QCursor.pos())
            dismiss_threshold_y = parent.height() / 2
            # 画面上半分にいる間はスイッチャーを戻さない
            if parent.rect().contains(parent_pos) and 0 <= parent_pos.y() < dismiss_threshold_y:
                return
        self._start_hide_timer()

    def _start_hide_timer(self) -> None:
        if not self.hide_timer.isActive():
            self.hide_timer.start()

    def _check_and_hide(self) -> None:
        parent = self.parentWidget()
        if parent:
            parent_pos = parent.mapFromGlobal(QCursor.pos())
            dismiss_threshold_y = parent.height() / 2
            if self.geometry().contains(parent_pos) or (
                parent.rect().contains(parent_pos) and 0 <= parent_pos.y() < dismiss_threshold_y
            ):
                return
        self.set_state(SwitcherState.HIDDEN)

    def _slide_left(self) -> None:
        self._slide_by(-self.SLIDE_STEP)

    def _slide_right(self) -> None:
        self._slide_by(self.SLIDE_STEP)

    def _slide_by(self, delta: int) -> None:
        bar = self.scroll_area.horizontalScrollBar()
        start_val = bar.value()
        target_val = max(bar.minimum(), min(bar.maximum(), start_val + delta))
        if target_val == start_val:
            return
        self.scroll_anim.stop()
        self.scroll_anim.setStartValue(start_val)
        self.scroll_anim.setEndValue(target_val)
        self.scroll_anim.start()

    def _on_scroll_anim_step(self, val: Any) -> None:
        self.scroll_area.horizontalScrollBar().setValue(int(val))

    def _update_arrow_buttons(self) -> None:
        bar = self.scroll_area.horizontalScrollBar()
        has_scroll = bar.maximum() > bar.minimum()
        self.btn_left.setEnabled(has_scroll and bar.value() > bar.minimum())
        self.btn_right.setEnabled(has_scroll and bar.value() < bar.maximum())

    def _clear_cards(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget:
                if hasattr(widget, "thumb_timer") and widget.thumb_timer.isActive():
                    widget.thumb_timer.stop()
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def activate(self) -> None:
        if not self.poll_timer.isActive():
            self.poll_timer.start()

    def deactivate(self) -> None:
        self.poll_timer.stop()
        self.hide_timer.stop()
        self.anim.stop()
        self.scroll_anim.stop()
        self.state = SwitcherState.HIDDEN
        self._offset = 0
        self.move(0, -self.BAR_HEIGHT)

    def refresh_records(self, current_filename: Optional[str]) -> None:
        self.current_filename = current_filename
        loader = RecordDataLoader(self.config)
        records_by_date, _, _ = loader.load_records()

        flat_records = []
        for records in records_by_date.values():
            flat_records.extend(records)
        # 左から右にかけて時間順に降順（最新→過去）で並べる
        flat_records.sort(key=lambda r: float(r.get("timestamp") or 0), reverse=True)

        self._all_records = [r for r in flat_records if r.get("filename") != self.current_filename]

        self._clear_cards()

        if not self._all_records:
            empty = QLabel("他の録画がありません")
            empty.setStyleSheet("color: #888888; font-size: 12px; background: transparent;")
            self.cards_layout.addWidget(empty)
            self.handle_bar.setText("▼ ほかの録画はありません ▼")
        else:
            for rec in self._all_records:
                card = SwitcherItemCard(rec, self.cards_container)
                card.clicked.connect(self._on_card_clicked)
                self.cards_layout.addWidget(card)
            self.handle_bar.setText(f"▼ ほかの録画 ({len(self._all_records)}件) ▼")

        self.cards_layout.addStretch()
        self.scroll_area.horizontalScrollBar().setValue(0)
        QTimer.singleShot(0, self._update_arrow_buttons)

    def _on_card_clicked(self, filename: str) -> None:
        self.set_state(SwitcherState.HIDDEN, immediate=True)
        # スイッチャー格納アニメーションとイベントループが安定した後に発火
        QTimer.singleShot(30, lambda: self.recordSelected.emit(filename))
