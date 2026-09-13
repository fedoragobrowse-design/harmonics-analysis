@echo off
setlocal
py -m pip install --upgrade pip
py -m pip install -r windows-requirements.txt
py -m PyInstaller --noconfirm --clean --windowed --onefile --name "Harmonics Analysis" --add-binary "ffmpeg.exe;." harmonic-viewer.py
