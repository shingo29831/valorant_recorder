"""Module: @role: 録画一覧の各試合カード（サムネイル・勝敗スコア・KDA・お気に入り・コンテキストメニュー）を描画・制御するウィジェット"""
import os
from typing import Optional
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
)
from PyQt6.QtCore import Qt, pyqtSignal, QByteArray, QTimer, QPoint, QSize
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen, QPixmapCache, QFontMetrics, QIcon
from PyQt6.QtSvg import QSvgRenderer

STAR_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#FFD700" stroke="#111111" stroke-width="1.5">
  <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
</svg>"""

STAR_EMPTY_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="rgba(0,0,0,0.35)" stroke="#E0E6ED" stroke-width="1.8">
  <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
</svg>"""

MORE_VERT_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#B8C4D0">
  <path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z"/>
</svg>"""


def _render_svg_icon(svg_bytes: bytes, size: int = 20) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class LoadingSpinner(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rotate)
        self.timer.start(50)
        self.setFixedSize(40, 40)

    def rotate(self) -> None:
        self.angle = (self.angle + 30) % 360
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(self.angle)

        pen = QPen(QColor("#FF4655"))
        pen.setWidth(4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(-12, -12, 24, 24, 0, 270 * 16)


class RecordItemWidget(QWidget):
    doubleClicked = pyqtSignal(str)
    renameRequested = pyqtSignal(str, str)
    deleteRequested = pyqtSignal(str)
    favoriteToggled = pyqtSignal(str)
    revealRequested = pyqtSignal(str)
    refetchLogRequested = pyqtSignal(str)
    selectionToggled = pyqtSignal()

    CARD_WIDTH = 268
    CARD_HEIGHT = 224
    THUMB_WIDTH = 252
    THUMB_HEIGHT = 142

    def __init__(
        self,
        json_filename: str,
        display_name: str,
        thumb_path: str,
        result: str,
        is_favorite: bool = False,
        mmr_change: int = 0,
        party_members: Optional[list] = None,
        is_fetching_api: bool = False,
        parent: Optional[QWidget] = None,
        record_data: Optional[dict] = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.json_filename = json_filename
        self.display_name = display_name
        self.result = (result or "unknown").lower()
        self.is_favorite = is_favorite
        self.mmr_change = mmr_change
        self.party_members = party_members or []
        self.is_fetching_api = is_fetching_api
        self.thumb_path = thumb_path
        self.record_data = record_data or {}
        self.game_mode = self.record_data.get("mode", "Unknown")

        self._press_pos: Optional[QPoint] = None
        self._emit_guard = False

        self.setFixedSize(self.CARD_WIDTH, self.CARD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        parsed = self._extract_structured_info()

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        # サムネイル領域
        self.thumb_label = QLabel()
        self.thumb_label.setObjectName("thumbContainer")
        self.thumb_label.setFixedSize(self.THUMB_WIDTH, self.THUMB_HEIGHT)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._thumb_retries = 0
        if self.thumb_path and os.path.exists(self.thumb_path):
            self._load_thumbnail()
        else:
            self.thumb_label.setText("NO PREVIEW")
            self.thumb_label.setStyleSheet(
                "QLabel#thumbContainer { background-color: #0B1219; color: #5A6B7C; font-size: 11px; font-weight: bold; border-radius: 6px; }"
            )
            if self.thumb_path:
                self.thumb_timer = QTimer(self)
                self.thumb_timer.timeout.connect(self._check_thumbnail)
                self.thumb_timer.start(1000)

        if self.is_fetching_api:
            self.overlay = QLabel(self.thumb_label)
            self.overlay.setFixedSize(self.THUMB_WIDTH, self.THUMB_HEIGHT)
            self.overlay.setStyleSheet("background-color: rgba(10, 16, 24, 165); border-radius: 6px;")
            self.spinner = LoadingSpinner(self.overlay)
            self.spinner.move((self.THUMB_WIDTH - 40) // 2, (self.THUMB_HEIGHT - 40) // 2)

        # サムネイル左上: お気に入りボタン (ワンクリック切替対応)
        self.fav_btn = QPushButton(self.thumb_label)
        self.fav_btn.setFixedSize(28, 28)
        self.fav_btn.move(6, 6)
        self.fav_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fav_btn.setToolTip("お気に入り切替")
        self.fav_btn.clicked.connect(self._on_fav_btn_clicked)
        self._update_fav_button_icon()

        # サムネイル右上: 勝敗・スコアバッジ
        self.result_badge = QLabel(self.thumb_label)
        badge_text, badge_bg, badge_fg = self._get_result_badge_spec(parsed["score"])
        self.result_badge.setText(badge_text)
        self.result_badge.setStyleSheet(f"""
            QLabel {{
                background-color: {badge_bg};
                color: {badge_fg};
                font-size: 11px;
                font-weight: 800;
                padding: 3px 8px;
                border-radius: 4px;
                letter-spacing: 0.5px;
            }}
        """)
        self.result_badge.adjustSize()
        self.result_badge.move(self.THUMB_WIDTH - self.result_badge.width() - 6, 6)

        # サムネイル左下: ゲームモード + 試合時間バッジ
        mode_text = parsed["mode"]
        if parsed["duration_str"]:
            mode_text = f"{mode_text} • {parsed['duration_str']}"
        self.mode_badge = QLabel(mode_text, self.thumb_label)
        self.mode_badge.setStyleSheet("""
            QLabel {
                background-color: rgba(12, 18, 26, 0.82);
                color: #D0D8E0;
                font-size: 10px;
                font-weight: 600;
                padding: 2px 6px;
                border-radius: 4px;
                border: 1px solid rgba(255, 255, 255, 0.12);
            }
        """)
        self.mode_badge.adjustSize()
        self.mode_badge.move(6, self.THUMB_HEIGHT - self.mode_badge.height() - 6)

        # サムネイル右下: MMR変動バッジ
        if self.mmr_change != 0:
            mmr_sign = f"+{self.mmr_change}" if self.mmr_change > 0 else f"{self.mmr_change}"
            mmr_fg = "#38EF7D" if self.mmr_change > 0 else "#FF6B78"
            mmr_border = "rgba(56, 239, 125, 0.45)" if self.mmr_change > 0 else "rgba(255, 107, 120, 0.45)"
            self.mmr_badge = QLabel(f"{mmr_sign} RR", self.thumb_label)
            self.mmr_badge.setStyleSheet(f"""
                QLabel {{
                    background-color: rgba(10, 16, 24, 0.88);
                    color: {mmr_fg};
                    font-size: 11px;
                    font-weight: bold;
                    padding: 2px 7px;
                    border-radius: 4px;
                    border: 1px solid {mmr_border};
                }}
            """)
            self.mmr_badge.adjustSize()
            self.mmr_badge.move(
                self.THUMB_WIDTH - self.mmr_badge.width() - 6,
                self.THUMB_HEIGHT - self.mmr_badge.height() - 6,
            )

        root_layout.addWidget(self.thumb_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        # 下部メタデータ領域 (2段構成)
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(2, 0, 2, 0)
        info_layout.setSpacing(4)

        # 1段目: メインタイトル (マップ • エージェント または カスタム名) + メニューボタン
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(4)

        self.name_label = QLabel()
        self.name_label.setStyleSheet(
            "color: #F2F5F8; font-size: 13px; font-weight: bold; background: transparent;"
        )
        primary_title = parsed["primary_title"]
        fm = QFontMetrics(self.name_label.font())
        self.name_label.setText(fm.elidedText(primary_title, Qt.TextElideMode.ElideRight, 218))
        self.name_label.setToolTip(self.display_name)
        top_row.addWidget(self.name_label, 1)

        self.menu_btn = QPushButton()
        self.menu_btn.setFixedSize(22, 22)
        self.menu_btn.setIcon(_render_svg_icon(MORE_VERT_SVG, 16))
        self.menu_btn.setIconSize(QSize(16, 16))
        self.menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.menu_btn.setToolTip("メニュー")
        self.menu_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 11px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.14);
            }
        """)
        self.menu_btn.clicked.connect(self._show_action_menu_from_btn)
        top_row.addWidget(self.menu_btn)

        info_layout.addLayout(top_row)

        # 2段目: KDA / サブ情報 + パーティバッジ + 時刻
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(6)

        if parsed["secondary_sub"]:
            sub_label = QLabel(parsed["secondary_sub"])
            sub_label.setStyleSheet(
                "color: #9FB1C4; font-size: 11px; font-weight: 600; background: transparent;"
            )
            bottom_row.addWidget(sub_label)

        if parsed["kda"]:
            kda_badge = QLabel(f"KDA  {parsed['kda']}")
            kda_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(255, 255, 255, 0.07);
                    color: #D6E2EE;
                    font-size: 11px;
                    font-weight: 600;
                    padding: 1px 6px;
                    border-radius: 3px;
                }
            """)
            bottom_row.addWidget(kda_badge)

        if self.party_members and len(self.party_members) > 1:
            self.party_label = QLabel(f"PT ×{len(self.party_members)}")
            self.party_label.setStyleSheet("""
                QLabel {
                    background-color: rgba(88, 166, 255, 0.16);
                    color: #79C0FF;
                    font-size: 10px;
                    font-weight: bold;
                    padding: 1px 5px;
                    border-radius: 3px;
                }
            """)
            self.party_label.setToolTip("Party:\n" + "\n".join(self.party_members))
            bottom_row.addWidget(self.party_label)

        bottom_row.addStretch()

        if parsed["time_str"]:
            time_label = QLabel(parsed["time_str"])
            time_label.setStyleSheet("color: #788A9C; font-size: 11px; background: transparent;")
            bottom_row.addWidget(time_label)

        info_layout.addLayout(bottom_row)
        root_layout.addLayout(info_layout)

        self._apply_card_theme()

        # 一括選択用チェックボックス
        self.checkbox_mode = "delete"
        self.checkbox = QPushButton(self)
        self.checkbox.setCheckable(True)
        self.checkbox.setFixedSize(26, 26)
        self.checkbox.setText("")
        self.checkbox.toggled.connect(self._on_checkbox_toggled)
        self.checkbox.move(self.width() - 34, 12)
        self.checkbox.hide()
        self._on_checkbox_toggled(False)

    def _extract_structured_info(self) -> dict:
        map_name = self.record_data.get("map_name", "")
        agent_name = self.record_data.get("agent_name", "")
        mode = self.record_data.get("mode", "")
        time_str = self.record_data.get("time_str", "")
        custom_name = self.record_data.get("custom_name", "")
        kda = self.record_data.get("kda", "")
        score = self.record_data.get("score", "")
        duration_str = self.record_data.get("duration_str", "")

        # record_data未指定時のフォールバック解析 ("Mode - Map - Agent - YYYY-MM-DD HH:MM")
        if not map_name and " - " in self.display_name:
            parts = [p.strip() for p in self.display_name.split(" - ")]
            if len(parts) >= 4:
                mode = mode or parts[0]
                map_name = parts[1]
                agent_name = parts[2]
                dt_part = parts[3]
                if " " in dt_part:
                    time_str = dt_part.split(" ")[-1]

        clean_agent = agent_name if agent_name and agent_name != "Unknown Agent" else ""
        clean_map = map_name if map_name and map_name != "Unknown" else ""

        if custom_name:
            primary_title = custom_name
            secondary_sub = " • ".join(x for x in (clean_map, clean_agent) if x)
        elif clean_map or clean_agent:
            primary_title = "  •  ".join(x for x in (clean_map, clean_agent) if x)
            secondary_sub = ""
        else:
            primary_title = self.display_name
            secondary_sub = ""

        return {
            "primary_title": primary_title,
            "secondary_sub": secondary_sub,
            "mode": mode or "Match",
            "time_str": time_str,
            "kda": kda,
            "score": score,
            "duration_str": duration_str,
        }

    def _get_result_badge_spec(self, score: str) -> tuple[str, str, str]:
        suffix = f"  {score}" if score else ""
        if self.result == "win":
            return f"WIN{suffix}", "rgba(22, 163, 74, 0.92)", "#FFFFFF"
        if self.result == "loss":
            return f"LOSS{suffix}", "rgba(225, 45, 57, 0.92)", "#FFFFFF"
        if self.result == "draw":
            return f"DRAW{suffix}", "rgba(120, 130, 145, 0.92)", "#FFFFFF"
        if self.result == "deathmatch":
            return f"DM{suffix}", "rgba(217, 119, 6, 0.92)", "#FFFFFF"
        return (score if score else "MATCH"), "rgba(45, 60, 78, 0.88)", "#D0D8E0"

    def _apply_card_theme(self) -> None:
        if self.result == "win":
            border_color = "rgba(46, 204, 113, 0.55)"
            hover_border = "#2ECC71"
            bg_color = "#132226"
            hover_bg = "#192E33"
        elif self.result == "loss":
            border_color = "rgba(255, 70, 85, 0.55)"
            hover_border = "#FF4655"
            bg_color = "#23161C"
            hover_bg = "#2D1C24"
        else:
            border_color = "rgba(100, 120, 140, 0.45)"
            hover_border = "#90A4AE"
            bg_color = "#151F2B"
            hover_bg = "#1D2A3A"

        self.setStyleSheet(f"""
            RecordItemWidget {{
                background-color: {bg_color};
                border: 1.5px solid {border_color};
                border-radius: 10px;
            }}
            RecordItemWidget:hover {{
                background-color: {hover_bg};
                border: 2px solid {hover_border};
            }}
        """)

    def _update_fav_button_icon(self) -> None:
        svg = STAR_SVG if self.is_favorite else STAR_EMPTY_SVG
        self.fav_btn.setIcon(_render_svg_icon(svg, 20))
        self.fav_btn.setIconSize(QSize(20, 20))
        self.fav_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(10, 16, 24, 0.65);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 14px;
            }
            QPushButton:hover {
                background-color: rgba(25, 35, 48, 0.92);
                border: 1px solid #FFD700;
            }
        """)

    def _on_fav_btn_clicked(self) -> None:
        self.is_favorite = not self.is_favorite
        self._update_fav_button_icon()
        self.favoriteToggled.emit(self.json_filename)

    def _load_thumbnail(self) -> None:
        cache_key = f"card_thumb_{self.thumb_path}"
        cached_pixmap = QPixmapCache.find(cache_key)
        if cached_pixmap is not None and not cached_pixmap.isNull():
            self.thumb_label.setPixmap(cached_pixmap)
            self.thumb_label.setStyleSheet("QLabel#thumbContainer { background-color: #0B1219; border-radius: 6px; }")
            return

        pixmap = QPixmap(self.thumb_path)
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(
                self.THUMB_WIDTH,
                self.THUMB_HEIGHT,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            if scaled_pixmap.width() > self.THUMB_WIDTH or scaled_pixmap.height() > self.THUMB_HEIGHT:
                x = max(0, (scaled_pixmap.width() - self.THUMB_WIDTH) // 2)
                y = max(0, (scaled_pixmap.height() - self.THUMB_HEIGHT) // 2)
                scaled_pixmap = scaled_pixmap.copy(x, y, self.THUMB_WIDTH, self.THUMB_HEIGHT)

            QPixmapCache.insert(cache_key, scaled_pixmap)
            self.thumb_label.setPixmap(scaled_pixmap)
            self.thumb_label.setStyleSheet("QLabel#thumbContainer { background-color: #0B1219; border-radius: 6px; }")
        else:
            self.thumb_label.setText("NO PREVIEW")
            self.thumb_label.setStyleSheet(
                "QLabel#thumbContainer { background-color: #0B1219; color: #5A6B7C; font-size: 11px; font-weight: bold; border-radius: 6px; }"
            )

    def _check_thumbnail(self) -> None:
        self._thumb_retries += 1
        if self.thumb_path and os.path.exists(self.thumb_path):
            self._load_thumbnail()
            self.thumb_timer.stop()
        elif self._thumb_retries >= 15:
            self.thumb_timer.stop()

    def cleanup(self) -> None:
        if hasattr(self, "thumb_timer") and self.thumb_timer.isActive():
            self.thumb_timer.stop()
        if hasattr(self, "spinner") and hasattr(self.spinner, "timer") and self.spinner.timer.isActive():
            self.spinner.timer.stop()

    def _on_checkbox_toggled(self, checked: bool) -> None:
        if checked:
            self.checkbox.setText("✓")
            color = "#FF4655" if self.checkbox_mode == "delete" else "#FFD700"
            text_color = "white" if self.checkbox_mode == "delete" else "black"
            self.checkbox.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color};
                    color: {text_color};
                    font-weight: bold;
                    font-size: 14px;
                    border: 2px solid #FFFFFF;
                    border-radius: 5px;
                }}
            """)
        else:
            self.checkbox.setText("")
            self.checkbox.setStyleSheet("""
                QPushButton {
                    background-color: rgba(10, 16, 24, 0.78);
                    border: 2px solid #FFFFFF;
                    border-radius: 5px;
                }
                QPushButton:hover {
                    background-color: rgba(255, 70, 85, 0.4);
                }
            """)
        self.selectionToggled.emit()

    def set_selection_mode(self, enabled: bool, mode: str = "delete") -> None:
        self.checkbox_mode = mode
        self.checkbox.setVisible(enabled)
        self.result_badge.setVisible(not enabled)
        if not enabled:
            self.checkbox.blockSignals(True)
            self.checkbox.setChecked(False)
            self._on_checkbox_toggled(False)
            self.checkbox.blockSignals(False)
        else:
            self.checkbox.raise_()
            self._on_checkbox_toggled(self.checkbox.isChecked())

    def set_checked(self, checked: bool) -> None:
        self.checkbox.setChecked(checked)

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def _emit_open_record(self) -> None:
        if self._emit_guard:
            return
        self._emit_guard = True
        QTimer.singleShot(250, self._reset_emit_guard)
        self.doubleClicked.emit(self.json_filename)

    def _reset_emit_guard(self) -> None:
        self._emit_guard = False

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.checkbox.isVisible():
                self.checkbox.setChecked(not self.checkbox.isChecked())
                event.accept()
                return
            self._press_pos = event.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.checkbox.isVisible():
            if self._press_pos is not None:
                dist = (event.pos() - self._press_pos).manhattanLength()
                self._press_pos = None
                if dist < 10 and self.rect().contains(event.pos()):
                    self._emit_open_record()
                    event.accept()
                    return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.checkbox.isVisible():
            self._emit_open_record()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _show_action_menu_from_btn(self) -> None:
        self._open_context_menu(self.menu_btn.mapToGlobal(QPoint(0, self.menu_btn.height())))

    def contextMenuEvent(self, event) -> None:
        self._open_context_menu(event.globalPos())

    def _needs_log_refetch(self) -> bool:
        if "needs_log_refetch" in self.record_data:
            return bool(self.record_data["needs_log_refetch"])
        map_name = self.record_data.get("map_name", "")
        agent_name = self.record_data.get("agent_name", "")
        return bool(
            self.result == "unknown"
            or self.game_mode in ("Unknown", "Custom", "")
            or map_name in ("Unknown", "Custom / Unknown", "Fetching...", "")
            or agent_name in ("Unknown Agent", "Unknown", "")
            or not self.record_data.get("kda")
        )

    def _open_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #16222F;
                color: #F0F4F8;
                border: 1px solid #2C3E50;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #FF4655;
                color: #FFFFFF;
            }
            QMenu::separator {
                height: 1px;
                background: #2C3E50;
                margin: 4px 6px;
            }
        """)
        play_action = menu.addAction("▶  再生 (Play)")
        fav_label = "★  お気に入り解除" if self.is_favorite else "☆  お気に入りに追加"
        fav_action = menu.addAction(fav_label)
        rename_action = menu.addAction("✎  名前を変更 (Rename)")
        reveal_action = menu.addAction("📁  保存先フォルダで表示")
        refetch_action = None
        if self._needs_log_refetch() or self.is_fetching_api:
            if self.is_fetching_api:
                refetch_action = menu.addAction("🔄  ログを取得中...")
                refetch_action.setEnabled(False)
            else:
                refetch_action = menu.addAction("🔄  ログを再取得 (Refetch Log)")
        menu.addSeparator()
        delete_action = menu.addAction("🗑  削除 (Delete)")

        action = menu.exec(global_pos)
        if action == play_action:
            self._emit_open_record()
        elif action == fav_action:
            self._on_fav_btn_clicked()
        elif action == rename_action:
            current = self.record_data.get("custom_name") or self.display_name
            self.renameRequested.emit(self.json_filename, current)
        elif action == reveal_action:
            self.revealRequested.emit(self.json_filename)
        elif refetch_action is not None and action == refetch_action:
            self.refetchLogRequested.emit(self.json_filename)
        elif action == delete_action:
            self.deleteRequested.emit(self.json_filename)
