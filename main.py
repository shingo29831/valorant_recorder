import sys
import traceback
import warnings
import logging
import os

# --- ロギング設定 ---
log_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValorantRecorder')
os.makedirs(log_dir, exist_ok=True)
log_file_path = os.path.join(log_dir, 'app.log')

# コンソール出力ハンドラは無限ループの原因になるため、ファイル出力のみに限定する
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(log_file_path, encoding='utf-8')]
)

class StreamToLogger:
    def __init__(self, logger, level):
        self.logger = logger
        self.level = level
        self._is_logging = False

    def write(self, buf):
        # logging内部からの再帰呼び出し(無限ループ)を防ぐ
        if self._is_logging:
            return
        self._is_logging = True
        try:
            if isinstance(buf, str):
                for line in buf.rstrip().splitlines():
                    if line.strip():
                        self.logger.log(self.level, line.rstrip())
        finally:
            self._is_logging = False

    def flush(self):
        pass

sys.stdout = StreamToLogger(logging.getLogger('STDOUT'), logging.INFO)
sys.stderr = StreamToLogger(logging.getLogger('STDERR'), logging.ERROR)

# loggingモジュール自体のエラー出力を無効化（無限ループ防止の念押し）
logging.raiseExceptions = False
# --------------------

# --- グローバルエラーハンドラ ---
# アプリがクラッシュした際に原因のログをダイアログで表示する
def global_exception_handler(exctype, value, tb):
    error_msg = "".join(traceback.format_exception(exctype, value, tb))
    logging.critical(f"Uncaught exception:\n{error_msg}")
    
    # 既にQApplicationが存在するかチェックし、なければ作成
    from PyQt6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
        
    msg_box = QMessageBox()
    msg_box.setIcon(QMessageBox.Icon.Critical)
    msg_box.setWindowTitle("Critical Error")
    msg_box.setText("An unexpected error occurred.\nPlease check the log file for details.")
    msg_box.setDetailedText(error_msg)
    msg_box.exec()
    sys.exit(1)

sys.excepthook = global_exception_handler
# ------------------------------

# soundcardライブラリから発生する不連続性の警告をグローバルに無視する
warnings.filterwarnings("ignore", message=".*data discontinuity.*")
warnings.filterwarnings("ignore", module=".*soundcard.*")

# UIや他のモジュールが読み込まれる前にsoundcardライブラリのバグを修正する
try:
    from core.patcher import patch_soundcard_lib
    patch_soundcard_lib()
except Exception as e:
    # Nuitka等のコンパイル済み環境ではソースコードが存在せずパッチに失敗するため無視する
    if sys.stderr is not None:
        try:
            print(f"Skipped soundcard patch: {e}", file=sys.stderr)
        except Exception:
            pass

import ctypes
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QIcon
from PyQt6.QtNetwork import QLocalSocket, QLocalServer
from PyQt6.QtCore import QTextStream
from ui.main_window import MainWindow, get_resource_path
from core.config import Config
from core.autostart import set_autostart

VALORANT_STYLE = """
QWidget {
    background-color: #0F1923;
    color: #ECE8E1;
    font-family: "Segoe UI", "Yu Gothic UI", "Meiryo UI", sans-serif;
    font-size: 14px;
}
QTabWidget::pane {
    border: 1px solid #333333;
    background-color: #0F1923;
}
QTabBar::tab {
    background: #1F2326;
    padding: 10px 24px;
    min-height: 22px;
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
QGroupBox {
    border: 1px solid #2E3842;
    border-radius: 6px;
    margin-top: 14px;
    padding-top: 14px;
    font-weight: bold;
    color: #ECE8E1;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 8px;
    color: #FF4655;
    font-size: 14px;
    font-weight: bold;
}
QPushButton {
    background-color: #FF4655;
    color: white;
    border: none;
    padding: 8px 18px;
    min-height: 22px;
    font-weight: bold;
    border-radius: 4px;
}
QPushButton:hover {
    background-color: #ff5e6b;
}
QPushButton:pressed {
    background-color: #d43744;
}
QPushButton:disabled {
    background-color: #3A3F45;
    color: #888888;
}
QLineEdit, QComboBox, QSpinBox, QListWidget {
    background-color: #1F2326;
    border: 1px solid #3A444E;
    border-radius: 4px;
    padding: 6px 10px;
    min-height: 22px;
    color: #ECE8E1;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #FF4655;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 28px;
    border-left: 1px solid #3A444E;
}
QComboBox QAbstractItemView {
    background-color: #1F2326;
    border: 1px solid #3A444E;
    color: #ECE8E1;
    selection-background-color: #FF4655;
    selection-color: #FFFFFF;
    padding: 4px;
    outline: 0px;
}
QComboBox QAbstractItemView::item {
    min-height: 28px;
    padding: 4px 8px;
}
QCheckBox {
    spacing: 8px;
    min-height: 24px;
    color: #ECE8E1;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 1px solid #55606B;
    border-radius: 3px;
    background-color: #1F2326;
}
QCheckBox::indicator:hover {
    border-color: #FF4655;
}
QCheckBox::indicator:checked {
    background-color: #FF4655;
    border-color: #FF4655;
}
QListWidget::item:selected {
    background-color: #FF4655;
    color: white;
}
QSlider {
    min-height: 26px;
}
QSlider::groove:horizontal {
    border: 1px solid #333333;
    height: 8px;
    background: #1F2326;
    margin: 2px 0;
    border-radius: 4px;
}
QSlider::sub-page:horizontal {
    background: #FF4655;
    border-radius: 4px;
}
QSlider::handle:horizontal {
    background: #ECE8E1;
    border: 2px solid #FF4655;
    width: 16px;
    margin: -5px 0;
    border-radius: 9px;
}
QSlider::handle:horizontal:hover {
    background: #FFFFFF;
    border-color: #FF6B78;
}
QScrollBar:vertical {
    background: #141E28;
    width: 10px;
    margin: 2px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #3A4652;
    min-height: 30px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #FF4655;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QStatusBar {
    background-color: #1F2326;
    color: #888888;
    border-top: 1px solid #333333;
}
"""

SERVER_NAME = "ValorantRecorderSingleInstance"

def main():
    # レジストリ自動起動時やNuitka onefile起動時でも一時フォルダではなく本来の配置先へ移動
    from core.config import get_app_base_dir
    app_dir = get_app_base_dir()
    if app_dir and os.path.isdir(app_dir):
        os.chdir(app_dir)

    # Windowsのタスクバーアイコンを正しく表示させるためのAppUserModelID設定
    if sys.platform == "win32":
        try:
            app_id = "ValoReco.App.1.0"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(get_resource_path("assets/icon.ico")))
    
    # 既にアプリが起動しているかチェック
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if socket.waitForConnected(500):
        if "--tray" not in sys.argv:
            # 通常起動された場合は起動済みインスタンスに "show" コマンドを送って終了する
            stream = QTextStream(socket)
            stream << "show"
            stream.flush()
            socket.waitForBytesWritten(500)
        return

    # 初回起動の場合はローカルサーバーを立ててコマンドを待ち受ける
    server = QLocalServer()
    server.removeServer(SERVER_NAME)
    if not server.listen(SERVER_NAME):
        print(f"Failed to start local server: {server.errorString()}")
        return
        
    app.setStyleSheet(VALORANT_STYLE)
    # 最後のウィンドウが閉じられてもアプリを終了しないように設定
    app.setQuitOnLastWindowClosed(False)
    
    config = Config()
    if config.AUTO_START:
        set_autostart(True)
    window = MainWindow(config)
    
    def handle_connection():
        client = server.nextPendingConnection()
        if client:
            if client.waitForReadyRead(1000):
                stream = QTextStream(client)
                msg = stream.readAll()
                if msg == "show":
                    window.show_window()
            client.disconnectFromServer()
            
    server.newConnection.connect(handle_connection)
    
    # コマンドライン引数に --tray が指定されていない場合のみ初回起動時にUIを表示する
    if "--tray" not in sys.argv:
        window.show()

    ret = app.exec()
    try:
        server.close()
    except Exception:
        pass
    # バックグラウンドプロセスのゾンビ残存を完全に防止しプロセスを終了
    os._exit(ret)

if __name__ == "__main__":
    main()
