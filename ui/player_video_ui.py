"""Module: @role: 動画プレイヤー画面のSVGアイコン定義およびウィジェット・レイアウト構築を担うUIモジュール"""
from dataclasses import dataclass
from PyQt6.QtCore import QByteArray, QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from core.i18n import Translations
from ui.clip_edit_panel import ClipEditPanel
from ui.event_toggle_widget import EventToggleWidget
from ui.player_components import ClickableVideoWidget, PlayerContainer
from ui.timeline_overlay import TimelineOverlay
from ui.timeline_sync_panel import TimelineSyncPanel
from ui.video_playback_controls import PlaybackControlsWidget
from ui.video_player_core import VideoPlayerCore
from ui.volume_widgets import ChatVolumeWidget, MicVolumeWidget, VolumeWidget

BACK_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M20,11V13H8L13.5,18.5L12.08,19.92L4.16,12L12.08,4.08L13.5,5.5L8,11H20Z" />
</svg>"""

SCISSORS_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M9.64,7.64C9.87,7.14 10,6.59 10,6C10,3.79 8.21,2 6,2C3.79,2 2,3.79 2,6C2,8.21 3.79,10 6,10C6.59,10 7.14,9.87 7.64,9.64L10,12L7.64,14.36C7.14,14.13 6.59,14 6,14C3.79,14 2,15.79 2,18C2,20.21 3.79,22 6,22C8.21,22 10,20.21 10,18C10,17.41 9.87,16.86 9.64,16.36L12,14L19,21H22V20L9.64,7.64M6,8C4.9,8 4,7.1 4,6C4,4.9 4.9,4 6,4C7.1,4 8,4.9 8,6C8,7.1 7.1,8 6,8M6,20C4.9,20 4,19.1 4,18C4,16.9 4.9,16 6,16C7.1,16 8,16.9 8,18C8,19.1 7.1,20 6,20M12,12.5C11.72,12.5 11.5,12.28 11.5,12C11.5,11.72 11.72,11.5 12,11.5C12.28,11.5 12.5,11.72 12.5,12C12.5,12.28 12.28,12.5 12,12.5M19,3H22V4L14,12L11.64,9.64L19,3Z" />
</svg>"""

ZOOM_IN_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/>
</svg>"""

ZOOM_OUT_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M19 13H5v-2h14v2z"/>
</svg>"""

STAR_FILLED_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#FFD700">
  <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
</svg>"""

STAR_OUTLINE_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="transparent" stroke="white" stroke-width="2">
  <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
</svg>"""

TRASH_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M16 9v10H8V9h8m-1.5-6h-5l-1 1H5v2h14V4h-3.5l-1-1zM18 7H6v12c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7z"/>
</svg>"""

SYNC_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M12,4V1L8,5l4,4V6c3.31,0,6,2.69,6,6c0,1.01-0.25,1.97-0.7,2.8l1.46,1.46C19.54,15.03,20,13.57,20,12 C20,7.58,16.42,4,12,4z M12,18c-3.31,0-6-2.69-6-6c0-1.01,0.25-1.97,0.7-2.8L5.24,7.74C4.46,8.97,4,10.43,4,12c0,4.42,3.58,8,8,8v3l4-4 l-4-4V18z"/>
</svg>"""

@dataclass
class PlayerVideoUi:
    content_widget: QWidget
    page_layout: QVBoxLayout
    left_layout: QVBoxLayout
    right_layout: QVBoxLayout
    back_btn: QPushButton
    timeline_overlay: TimelineOverlay
    controls_widget: QWidget
    volume_widget: VolumeWidget
    mic_volume_widget: MicVolumeWidget
    chat_volume_widget: ChatVolumeWidget
    time_label: QLabel
    zoom_out_btn: QPushButton
    zoom_in_btn: QPushButton
    player_container: PlayerContainer
    edit_panel: ClipEditPanel
    sync_panel: TimelineSyncPanel
    edit_mode_btn: QPushButton
    fav_btn: QPushButton
    delete_btn: QPushButton
    sync_btn: QPushButton
    event_toggle_widget: EventToggleWidget
    playback_controls: PlaybackControlsWidget
    speed_osd_label: QLabel

def create_svg_icon(svg_bytes: bytes, size: int = 24) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)

def _create_side_button(
    svg_bytes: bytes,
    icon_size: int,
    tooltip: str = "",
    hover_color: str = "",
) -> QPushButton:
    btn = QPushButton()
    btn.setFixedSize(40, 40)
    if hover_color:
        btn.setStyleSheet(
            f"QPushButton {{ border-radius: 20px; background-color: #333333; }}"
            f"QPushButton:hover {{ background-color: {hover_color}; }}"
        )
    else:
        btn.setStyleSheet("border-radius: 20px; background-color: #333333;")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setIcon(create_svg_icon(svg_bytes, icon_size))
    btn.setIconSize(QSize(icon_size, icon_size))
    if tooltip:
        btn.setToolTip(tooltip)
    return btn

def _create_zoom_button(svg_bytes: bytes) -> QPushButton:
    btn = QPushButton()
    btn.setFixedSize(24, 24)
    btn.setStyleSheet("border-radius: 12px; background-color: #333333;")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setIcon(create_svg_icon(svg_bytes, 16))
    btn.hide()
    return btn

def build_player_video_ui(
    page: QWidget,
    t: Translations,
    video_widget: ClickableVideoWidget,
    player_core: VideoPlayerCore,
) -> PlayerVideoUi:
    # 動画サイズを変えずに画面全体を下へスライドさせるためのコンテナ
    content_widget = QWidget(page)
    content_widget.setMouseTracking(True)
    page_layout = QVBoxLayout(content_widget)
    page_layout.setContentsMargins(20, 20, 20, 20)
    page_layout.setSpacing(10)

    top_layout = QHBoxLayout()
    top_layout.setContentsMargins(0, 0, 0, 0)
    top_layout.setSpacing(10)

    left_container = QWidget()
    left_container.setFixedWidth(50)
    left_layout = QVBoxLayout(left_container)
    left_layout.setContentsMargins(0, 0, 0, 0)

    back_btn = _create_side_button(BACK_SVG, 24)
    left_layout.addWidget(back_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
    left_layout.addStretch()

    timeline_overlay = TimelineOverlay()
    controls_widget = QWidget()
    controls_layout = QHBoxLayout(controls_widget)
    controls_layout.setContentsMargins(0, 0, 0, 0)

    volume_widget = VolumeWidget()
    volume_widget.setToolTip(t.system_volume)
    volume_widget.set_volume(int(round(player_core.current_sys_volume * 100)))

    mic_volume_widget = MicVolumeWidget()
    mic_volume_widget.setToolTip(t.mic_volume)
    mic_volume_widget.set_volume(int(round(player_core.current_mic_volume * 100)))

    chat_volume_widget = ChatVolumeWidget()
    chat_volume_widget.setToolTip(getattr(t, "chat_volume", "Chat Volume"))
    chat_volume_widget.set_volume(int(round(player_core.current_chat_volume * 100)))
    chat_volume_widget.setVisible(False)

    time_label = QLabel("00:00 / 00:00")
    time_label.setFixedWidth(100)
    time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

    zoom_out_btn = _create_zoom_button(ZOOM_OUT_SVG)
    zoom_in_btn = _create_zoom_button(ZOOM_IN_SVG)

    controls_layout.addWidget(volume_widget)
    controls_layout.addWidget(mic_volume_widget)
    controls_layout.addWidget(chat_volume_widget)
    controls_layout.addWidget(time_label)
    controls_layout.addWidget(timeline_overlay)
    controls_layout.addWidget(zoom_out_btn)
    controls_layout.addWidget(zoom_in_btn)

    player_container = PlayerContainer(video_widget)
    player_container.setMouseTracking(True)

    edit_panel = ClipEditPanel(t)
    edit_panel.setVisible(False)

    sync_panel = TimelineSyncPanel(t)
    sync_panel.setVisible(False)

    right_container = QWidget()
    right_container.setFixedWidth(50)
    right_layout = QVBoxLayout(right_container)
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.setSpacing(10)

    edit_mode_btn = _create_side_button(SCISSORS_SVG, 24, tooltip=t.create_clip)
    fav_btn = _create_side_button(
        STAR_OUTLINE_SVG, 22, tooltip=getattr(t, "filter_favorite", "お気に入り"), hover_color="#444444"
    )
    delete_btn = _create_side_button(
        TRASH_SVG, 22, tooltip=getattr(t, "confirm_deletion_title", "削除"), hover_color="#FF4655"
    )
    sync_btn = _create_side_button(
        SYNC_SVG, 22, tooltip=getattr(t, "sync_timeline_tooltip", "タイムライン同期の微調整"), hover_color="#444444"
    )

    event_toggle_widget = EventToggleWidget()

    right_layout.addWidget(edit_mode_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
    right_layout.addWidget(sync_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
    right_layout.addWidget(fav_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
    right_layout.addWidget(delete_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
    right_layout.addStretch()
    right_layout.addWidget(event_toggle_widget, alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)

    top_layout.addWidget(left_container)
    top_layout.addWidget(player_container, stretch=1)
    top_layout.addWidget(right_container)

    playback_controls = PlaybackControlsWidget()

    page_layout.addLayout(top_layout, stretch=1)
    page_layout.addWidget(controls_widget)
    page_layout.addWidget(playback_controls)
    page_layout.addWidget(edit_panel)
    page_layout.addWidget(sync_panel)

    speed_osd_label = QLabel("1.0x", player_container)
    speed_osd_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    speed_osd_label.setStyleSheet(
        "background-color: rgba(0, 0, 0, 150); color: white; "
        "font-size: 24px; font-weight: bold; border-radius: 10px; padding: 10px;"
    )
    speed_osd_label.hide()

    return PlayerVideoUi(
        content_widget=content_widget,
        page_layout=page_layout,
        left_layout=left_layout,
        right_layout=right_layout,
        back_btn=back_btn,
        timeline_overlay=timeline_overlay,
        controls_widget=controls_widget,
        volume_widget=volume_widget,
        mic_volume_widget=mic_volume_widget,
        chat_volume_widget=chat_volume_widget,
        time_label=time_label,
        zoom_out_btn=zoom_out_btn,
        zoom_in_btn=zoom_in_btn,
        player_container=player_container,
        edit_panel=edit_panel,
        sync_panel=sync_panel,
        edit_mode_btn=edit_mode_btn,
        fav_btn=fav_btn,
        delete_btn=delete_btn,
        sync_btn=sync_btn,
        event_toggle_widget=event_toggle_widget,
        playback_controls=playback_controls,
        speed_osd_label=speed_osd_label,
    )
