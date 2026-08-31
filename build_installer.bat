@echo off
echo ==========================================
echo 1. Installing Python requirements...
echo ==========================================
pip install -r requirements.txt

echo.
echo ==========================================
echo 1.5. Preparing AI Models...
echo ==========================================
python setup_deepfilter.py

echo.
echo ==========================================
echo 2. Building executable with Nuitka...
echo ==========================================
if not exist "cpp\audio_processor\build\Release\audio_processor.dll" (
    echo [Error] C++ Audio Processor DLL not found!
    echo Please build the C++ module in Release mode before running this script.
    echo cd cpp\audio_processor ^&^& mkdir build ^&^& cd build ^&^& cmake .. ^&^& cmake --build . --config Release
    pause
    exit /b 1
)

REM Compile to a single executable using Nuitka.
REM Includes QtMultimedia plugin, soundcard dependencies, auth.key, C++ DLLs, and AI models.
REM Using --windows-console-mode=disable for production.
REM Nuitka excludes .dll files in --include-data-dir by default, so we force include them using --include-data-files with wildcards.
python -m nuitka --standalone --onefile --file-reference-choice=runtime --enable-plugin=pyqt6 --include-qt-plugins=multimedia --include-package-data=soundcard --include-data-file=auth.key=auth.key --include-data-dir=assets=assets --include-data-files=cpp/audio_processor/build/Release/*.dll=cpp/audio_processor/build/Release/ --include-data-dir=cpp/audio_processor/models=models --windows-console-mode=disable --windows-icon-from-ico=assets/icon.ico -o ValoReco.exe main.py

if %ERRORLEVEL% NEQ 0 (
    echo [Error] Nuitka build failed!
    pause
    exit /b 1
)

REM Nuitka output file defaults to ValoReco.exe
if not exist "ValoReco.exe" (
    echo [Error] Build failed! ValoReco.exe not found.
    pause
    exit /b 1
)

echo.
echo ==========================================
echo 3. Creating Installer with Inno Setup...
echo ==========================================
REM Inno Setup compiler path
set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe"
if not exist %ISCC% (
    set ISCC="C:\Program Files\Inno Setup 7\ISCC.exe"
)

if not exist %ISCC% (
    echo [Warning] Inno Setup not found.
    echo Please install Inno Setup from https://jrsoftware.org/isinfo.php to create the installer.
    echo The standalone main.exe is ready to use.
    pause
    exit /b 0
)

REM Compile installer.iss
%ISCC% installer.iss

echo.
echo ==========================================
echo Build and Packaging Complete!
echo You can find the installer in the "Output" folder.
echo ==========================================
pause
