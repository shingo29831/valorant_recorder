"""Module: @role: 環境テストの実行中進捗（チェックリスト・プログレスバー）および診断結果を表示するダイアログ"""
from typing import Optional
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from core.config import Config
from core.env_tester import EnvTesterThread, generate_diagnostic_report, STEP_FFMPEG, STEP_ENCODER, STEP_AUDIO, STEP_LOG, STEP_PIPELINE, TOTAL_STEPS
from core.i18n import Translations
from ui.settings_general import open_log_folder


class EnvTestItemRow(QWidget):
    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.title_text = title
        
        self.setStyleSheet("""
            QWidget {
                background-color: #1A242D;
                border: 1px solid #2D3D4D;
                border-radius: 6px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        self.icon_label = QLabel("⏳")
        self.icon_label.setStyleSheet("border: none; font-size: 15px;")
        self.icon_label.setFixedWidth(24)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.setContentsMargins(0, 0, 0, 0)

        self.title_label = QLabel(self.title_text)
        self.title_label.setStyleSheet("border: none; color: #ECE8E1; font-size: 13px; font-weight: bold;")
        text_layout.addWidget(self.title_label)

        self.detail_label = QLabel("")
        self.detail_label.setStyleSheet("border: none; color: #94A3B8; font-size: 11px;")
        self.detail_label.hide()
        text_layout.addWidget(self.detail_label)

        layout.addLayout(text_layout, 1)

        self.badge_label = QLabel("")
        self.badge_label.setStyleSheet("border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: bold;")
        self.badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.badge_label)

    def set_waiting(self, badge_text: str) -> None:
        self.icon_label.setText("⏳")
        self.detail_label.hide()
        self.detail_label.setText("")
        self.badge_label.setText(badge_text)
        self.badge_label.setStyleSheet("background-color: #2D3748; color: #A0AEC0; border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: bold;")

    def set_running(self, badge_text: str) -> None:
        self.icon_label.setText("🔄")
        self.detail_label.hide()
        self.detail_label.setText("")
        self.badge_label.setText(badge_text)
        self.badge_label.setStyleSheet("background-color: #1E3A5F; color: #60A5FA; border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: bold;")

    def set_passed(self, badge_text: str, summary: str = "") -> None:
        self.icon_label.setText("✅")
        if summary:
            self.detail_label.setText(summary)
            self.detail_label.show()
        else:
            self.detail_label.hide()
        self.badge_label.setText(badge_text)
        self.badge_label.setStyleSheet("background-color: #064E3B; color: #34D399; border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: bold;")

    def set_failed(self, badge_text: str, summary: str = "") -> None:
        self.icon_label.setText("❌")
        if summary:
            self.detail_label.setText(summary)
            self.detail_label.show()
        else:
            self.detail_label.hide()
        self.badge_label.setText(badge_text)
        self.badge_label.setStyleSheet("background-color: #7F1D1D; color: #F87171; border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: bold;")


class EnvTestDialog(QDialog):
    def __init__(self, config: Config, t: Translations, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        self.t = t
        self.tester_thread: Optional[EnvTesterThread] = None
        self.last_errors: list = []

        self.setWindowTitle(getattr(self.t, 'env_test_dialog_title', "Environment Test"))
        self.setFixedSize(580, 560)
        self.setModal(True)
        self.setStyleSheet("""
            QDialog {
                background-color: #0F1923;
                color: #ECE8E1;
            }
            QLabel {
                color: #ECE8E1;
            }
            QPushButton {
                background-color: #1A242D;
                color: #ECE8E1;
                border: 1px solid #3A4550;
                border-radius: 4px;
                padding: 6px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2D3D4D;
            }
            QPushButton:disabled {
                color: #556677;
                background-color: #121A21;
                border-color: #222B35;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self.title_label = QLabel(getattr(self.t, 'env_test_dialog_title', "Environment Test"))
        self.title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #ECE8E1;")
        layout.addWidget(self.title_label)

        self.status_label = QLabel(getattr(self.t, 'env_test_diagnosing', "Diagnosing environment..."))
        self.status_label.setStyleSheet("font-size: 12px; color: #94A3B8;")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(18)
        self._set_progress_style("#00B4D8")
        layout.addWidget(self.progress_bar)

        checklist_container = QVBoxLayout()
        checklist_container.setSpacing(8)

        self.rows = {
            STEP_FFMPEG: EnvTestItemRow(getattr(self.t, 'env_item_ffmpeg', "FFmpeg Executable"), self),
            STEP_ENCODER: EnvTestItemRow(getattr(self.t, 'env_item_encoder', "Video Encoder"), self),
            STEP_AUDIO: EnvTestItemRow(getattr(self.t, 'env_item_audio', "Audio Devices"), self),
            STEP_LOG: EnvTestItemRow(getattr(self.t, 'env_item_log', "VALORANT Log Access"), self),
            STEP_PIPELINE: EnvTestItemRow(getattr(self.t, 'env_item_pipeline', "Recording Pipeline"), self),
        }

        for row in self.rows.values():
            checklist_container.addWidget(row)
        layout.addLayout(checklist_container)

        self.error_text = QTextEdit()
        self.error_text.setReadOnly(True)
        self.error_text.setStyleSheet("""
            QTextEdit {
                background-color: #141E28;
                border: 1px solid #7F1D1D;
                border-radius: 4px;
                color: #FCA5A5;
                font-family: Consolas, monospace;
                font-size: 11px;
                padding: 6px;
            }
        """)
        self.error_text.hide()
        layout.addWidget(self.error_text, 1)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.copy_btn = QPushButton(getattr(self.t, 'copy_diagnostic_report', "Copy Diagnostic Report"))
        self.copy_btn.clicked.connect(self._copy_report)
        self.copy_btn.hide()
        btn_layout.addWidget(self.copy_btn)

        self.open_log_btn = QPushButton(getattr(self.t, 'open_log_folder', "Open Log Folder"))
        self.open_log_btn.clicked.connect(lambda: open_log_folder(self, self.t))
        btn_layout.addWidget(self.open_log_btn)

        btn_layout.addStretch()

        self.retest_btn = QPushButton(getattr(self.t, 'retest', "Retest"))
        self.retest_btn.clicked.connect(self.start_test)
        self.retest_btn.setEnabled(False)
        btn_layout.addWidget(self.retest_btn)

        self.close_btn = QPushButton(getattr(self.t, 'close', "Close"))
        self.close_btn.clicked.connect(self.accept)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF4655;
                color: #FFFFFF;
                border: none;
            }
            QPushButton:hover {
                background-color: #E03E4D;
            }
            QPushButton:disabled {
                background-color: #4A2328;
                color: #88666A;
            }
        """)
        self.close_btn.setEnabled(False)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)

        QTimer.singleShot(100, self.start_test)

    def _set_progress_style(self, chunk_color: str) -> None:
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: #141E28;
                border: 1px solid #2D3D4D;
                border-radius: 4px;
                color: #ECE8E1;
                text-align: center;
                font-size: 10px;
                font-weight: bold;
            }}
            QProgressBar::chunk {{
                background-color: {chunk_color};
                border-radius: 3px;
            }}
        """)

    def start_test(self) -> None:
        # 他のスレッドが終了するのを安全に待機
        if EnvTesterThread.is_test_running():
            self.status_label.setText(getattr(self.t, 'env_test_running', "Waiting for previous test to finish..."))
            QTimer.singleShot(300, self.start_test)
            return

        self.last_errors = []
        self.error_text.hide()
        self.copy_btn.hide()
        self.retest_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self._set_progress_style("#00B4D8")
        self.status_label.setText(getattr(self.t, 'env_test_diagnosing', "Diagnosing environment..."))

        waiting_str = getattr(self.t, 'env_status_waiting', "Waiting")
        for row in self.rows.values():
            row.set_waiting(waiting_str)

        self.tester_thread = EnvTesterThread(self.config, dry_run_recording=True)
        self.tester_thread.step_started.connect(self._on_step_started)
        self.tester_thread.step_finished.connect(self._on_step_finished)
        self.tester_thread.finished_signal.connect(self._on_all_finished)
        self.tester_thread.start()

    def _on_step_started(self, step_idx: int, step_key: str) -> None:
        if step_idx in self.rows:
            self.rows[step_idx].set_running(getattr(self.t, 'env_status_running', "Testing..."))
        target_val = int((step_idx / TOTAL_STEPS) * 100) + 5
        self.progress_bar.setValue(min(95, target_val))
        self.status_label.setText(f"{getattr(self.t, 'env_test_diagnosing', 'Diagnosing...')} ({step_idx + 1} / {TOTAL_STEPS})")

    def _on_step_finished(self, step_idx: int, step_key: str, success: bool, summary: str, detail: str) -> None:
        if step_idx in self.rows:
            if success:
                self.rows[step_idx].set_passed(getattr(self.t, 'env_status_passed', "Passed"), summary)
            else:
                self.rows[step_idx].set_failed(getattr(self.t, 'env_status_failed', "Failed"), summary)
        target_val = int(((step_idx + 1) / TOTAL_STEPS) * 100)
        self.progress_bar.setValue(min(100, target_val))

    def _on_all_finished(self, successes: list, errors: list) -> None:
        self.last_errors = errors
        self.progress_bar.setValue(100)
        self.retest_btn.setEnabled(True)
        self.close_btn.setEnabled(True)

        if not errors:
            self._set_progress_style("#10B981")
            self.status_label.setText(getattr(self.t, 'env_test_all_passed', "All environment tests passed."))
            self.status_label.setStyleSheet("font-size: 12px; color: #34D399; font-weight: bold;")
        else:
            self._set_progress_style("#EF4444")
            self.status_label.setText(getattr(self.t, 'env_test_some_failed', "Issues were detected in some tests."))
            self.status_label.setStyleSheet("font-size: 12px; color: #F87171; font-weight: bold;")

            error_details = []
            for title, desc in errors:
                error_details.append(f"❌ {title}\n{desc}".strip())
            self.error_text.setPlainText("\n\n" + ("=" * 40) + "\n\n".join(error_details))
            self.error_text.show()
            self.copy_btn.show()

    def _copy_report(self) -> None:
        report = generate_diagnostic_report(self.config, test_errors=self.last_errors)
        QGuiApplication.clipboard().setText(report)
        copied_text = getattr(self.t, 'copied_to_clipboard', "Copied!")
        self.copy_btn.setText(f"✓ {copied_text}")
        QTimer.singleShot(2000, lambda: self.copy_btn.setText(getattr(self.t, 'copy_diagnostic_report', "Copy Diagnostic Report")))

    def closeEvent(self, event) -> None:
        if self.tester_thread and self.tester_thread.isRunning():
            self.tester_thread.cancel()
            self.tester_thread.wait(1000)
        super().closeEvent(event)
