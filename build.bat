@echo off
echo Installing requirements...
pip install -r requirements.txt

echo Building executable with Nuitka...
REM Compile to a single executable using Nuitka.
REM Requires a C compiler (GCC or MSVC) installed.
python -m nuitka --standalone --onefile --enable-plugin=pyqt6 --include-qt-plugins=multimedia --include-package-data=soundcard --include-data-file=auth.key=auth.key --windows-console-mode=disable main.py

echo Build complete!
pause
