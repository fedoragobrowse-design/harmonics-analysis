@echo off
setlocal
py -m pip install --upgrade pip
py -m pip install -r windows-requirements.txt
py -m PyInstaller --noconfirm --clean --windowed --onefile --name "Harmonics Analysis" --hidden-import sounddevice --collect-binaries sounddevice --add-binary "ffmpeg.exe;." harmonic-viewer.py
py -m PyInstaller --noconfirm --clean --console --onefile --name "Harmonics Analysis MCP" harmonics-mcp.py
