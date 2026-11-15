@echo off
setlocal enabledelayedexpansion
set "NO_PAUSE="
set "FORCE_FLAG="

for %%A in (%*) do (
    if /I "%%~A"=="--no-pause" set "NO_PAUSE=1"
    if /I "%%~A"=="--force" set "FORCE_FLAG=--force"
)

if not defined NUMBER_OF_PROCESSORS set "NUMBER_OF_PROCESSORS=4"

echo ==========================================
echo 1. Checking Python requirements...
echo ==========================================
python tools\build_cache.py check pip %FORCE_FLAG%
if errorlevel 1 (
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [Error] pip install failed!
        if not defined NO_PAUSE pause
        exit /b 1
    )
    python tools\build_cache.py mark pip
)

echo.
echo ==========================================
echo 1.5. Checking AI Models ^& DeepFilterNet...
echo ==========================================
python tools\build_cache.py check deepfilter %FORCE_FLAG%
if errorlevel 1 (
    python setup_deepfilter.py
    if errorlevel 1 (
        echo [Error] setup_deepfilter.py failed!
        if not defined NO_PAUSE pause
        exit /b 1
    )
    python tools\build_cache.py mark deepfilter
)

echo.
echo ==========================================
echo 1.8. Checking C++ Audio Processor DLLs...
echo ==========================================
python tools\build_cache.py check cpp %FORCE_FLAG%
if errorlevel 1 (
    where cmake >nul 2>nul
    if !errorlevel! equ 0 (
        if not exist "cpp\audio_processor\build\CMakeCache.txt" (
            cmake -S "cpp\audio_processor" -B "cpp\audio_processor\build"
            if errorlevel 1 (
                echo [Error] CMake configure failed!
                if not defined NO_PAUSE pause
                exit /b 1
            )
        )
        cmake --build "cpp\audio_processor\build" --config Release --parallel
        if errorlevel 1 (
            echo [Error] C++ build failed!
            if not defined NO_PAUSE pause
            exit /b 1
        )
        python tools\build_cache.py mark cpp
    ) else (
        echo [Warning] cmake not found; verifying prebuilt DLLs...
    )
)

if not exist "cpp\audio_processor\build\Release\audio_processor.dll" (
    echo [Error] C++ Audio Processor DLL not found!
    echo Please build the C++ module in Release mode before running this script.
    echo cd cpp\audio_processor ^&^& mkdir build ^&^& cd build ^&^& cmake .. ^&^& cmake --build . --config Release
    if not defined NO_PAUSE pause
    exit /b 1
)

echo.
echo ==========================================
echo 2. Building executable with Nuitka...
echo ==========================================
set "NUITKA_OPTS=--standalone --onefile --lto=no --jobs=%NUMBER_OF_PROCESSORS% --file-reference-choice=runtime --enable-plugin=pyqt6 --include-qt-plugins=multimedia --include-package-data=soundcard --include-data-file=auth.key=auth.key --include-data-dir=assets=assets --include-data-files=cpp/audio_processor/build/Release/*.dll=cpp/audio_processor/build/Release/ --include-data-dir=cpp/audio_processor/models=cpp/audio_processor/models --windows-console-mode=disable --windows-icon-from-ico=assets/icon.ico"

python tools\build_cache.py check nuitka --extra "%NUITKA_OPTS%" %FORCE_FLAG%
if errorlevel 1 (
    python -m nuitka %NUITKA_OPTS% -o ValoReco.exe main.py
    if errorlevel 1 (
        echo [Error] Nuitka build failed!
        if not defined NO_PAUSE pause
        exit /b 1
    )
    if not exist "ValoReco.exe" (
        echo [Error] Build failed! ValoReco.exe not found.
        if not defined NO_PAUSE pause
        exit /b 1
    )
    python tools\build_cache.py mark nuitka --extra "%NUITKA_OPTS%"
)

echo.
echo ==========================================
echo 3. Creating Installer with Inno Setup...
echo ==========================================
set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe"
if not exist %ISCC% (
    set ISCC="C:\Program Files\Inno Setup 7\ISCC.exe"
)

if not exist %ISCC% (
    echo [Warning] Inno Setup not found.
    echo Please install Inno Setup from https://jrsoftware.org/isinfo.php to create the installer.
    echo The standalone ValoReco.exe is ready to use.
    if not defined NO_PAUSE pause
    exit /b 0
)

python tools\build_cache.py check inno %FORCE_FLAG%
if errorlevel 1 (
    %ISCC% installer.iss
    if errorlevel 1 (
        echo [Error] Inno Setup compilation failed!
        if not defined NO_PAUSE pause
        exit /b 1
    )
    python tools\build_cache.py mark inno
)

echo.
echo ==========================================
echo Build and Packaging Complete!
echo You can find the installer in the "Output" folder.
echo ==========================================
if not defined NO_PAUSE pause
