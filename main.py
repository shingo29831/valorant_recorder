import sys
from core.patcher import patch_soundcard_lib

# UIや他のモジュールが読み込まれる前にsoundcardライブラリのバグを修正する
patch_soundcard_lib()

from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow

VALORANT_STYLE = """
QWidget {
    background-color: #0F1923;
    color: #ECE8E1;
    font-family: "Segoe UI", sans-serif;
    font-size: 14px;
}
QTabWidget::pane {
    border: 1px solid #333333;
    background-color: #0F1923;
}
QTabBar::tab {
    background: #1F2326;
    padding: 12px 24px;
    border: 1px solid #333333;
    border-bottom: none;
    color: #888888;
    font-weight: bold;
}
QTabBar::tab:selected {
    background: #FF4655;
    color: #FFFFFF;
}
QTabBar::tab:hover:!selected {
    background: #2A2E33;
    color: #ECE8E1;
}
QPushButton {
    background-color: #FF4655;
    color: white;
    border: none;
    padding: 10px 20px;
    font-weight: bold;
    border-radius: 2px;
}
QPushButton:hover {
    background-color: #ff5e6b;
}
QPushButton:pressed {
    background-color: #d43744;
}
QLineEdit, QComboBox, QListWidget {
    background-color: #1F2326;
    border: 1px solid #333333;
    padding: 8px;
    color: #ECE8E1;
}
QListWidget::item:selected {
    background-color: #FF4655;
    color: white;
}
QSlider::groove:horizontal {
    border: 1px solid #333333;
    height: 8px;
    background: #1F2326;
    margin: 2px 0;
}
QSlider::handle:horizontal {
    background: #FF4655;
    border: 1px solid #FF4655;
    width: 14px;
    margin: -4px 0;
    border-radius: 7px;
}
QStatusBar {
    background-color: #1F2326;
    color: #888888;
    border-top: 1px solid #333333;
}
"""

def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(VALORANT_STYLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()