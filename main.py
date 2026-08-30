import sys
import traceback
import warnings

# --- グローバルエラーハンドラ ---
# アプリがクラッシュした際に原因のログをダイアログで表示する
def global_exception_handler(exctype, value, tb):
    error_msg = "".join(traceback.format_exception(exctype, value, tb))
    
    # コンソール非表示環境で sys.stderr が存在しない場合のクラッシュを防ぐ
    if sys.stderr is not None:
        try:
            print(error_msg, file=sys.stderr)
        except Exception:
            pass
    
    # 既にQApplicationが存在するかチェックし、なければ作成
    from PyQt6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
        
    msg_box = QMessageBox()
    msg_box.setIcon(QMessageBox.Icon.Critical)
    msg_box.setWindowTitle("Critical Error")
    msg_box.setText("An unexpected error occurred during startup:")
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

SERVER_NAME = "ValorantRecorderSingleInstance"

def main():
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
        # 起動済みの場合は "show" コマンドを送って自身のプロセスは終了する
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
    
    window = MainWindow()
    config = Config()
    
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
        
    sys.exit(app.exec())

if __name__ == "__main__":
    main()