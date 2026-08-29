@echo off
echo ==========================================
echo 1. Installing Python requirements...
echo ==========================================
pip install -r requirements.txt

echo.
echo ==========================================
echo 2. Building executable with Nuitka...
echo ==========================================
:: Nuitkaを使用してCコンパイルし、単一の実行ファイルを生成します。
python -m nuitka --standalone --onefile --enable-plugin=pyqt6 --windows-console-mode=disable main.py

:: Nuitkaの出力ファイル名はデフォルトで main.exe になります。
if not exist "main.exe" (
    echo [Error] Build failed! main.exe not found.
    pause
    exit /b 1
)

echo.
echo ==========================================
echo 3. Creating Installer with Inno Setup...
echo ==========================================
:: Inno Setupのコンパイラパス (デフォルトのインストール先)
set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

if not exist %ISCC% (
    echo [Warning] Inno Setup not found at %ISCC%.
    echo Please install Inno Setup from https://jrsoftware.org/isinfo.php to create the installer.
    echo The standalone main.exe is ready to use.
    pause
    exit /b 0
)

:: installer.iss をコンパイルして Output フォルダに Setup.exe を生成
%ISCC% installer.iss

echo.
echo ==========================================
echo Build and Packaging Complete!
echo You can find the installer in the "Output" folder.
echo ==========================================
pause
