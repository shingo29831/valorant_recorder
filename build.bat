@echo off
echo Installing requirements...
pip install -r requirements.txt

echo Building executable with Nuitka...
:: Nuitkaを使用してCコンパイルし、単一の実行ファイルを生成します。
:: ※実行にはCコンパイラ(GCCまたはMSVC)がインストールされている必要があります。
python -m nuitka --standalone --onefile --enable-plugin=pyqt6 --windows-console-mode=disable main.py

echo Build complete!
pause
