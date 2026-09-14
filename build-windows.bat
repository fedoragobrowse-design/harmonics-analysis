@echo off
setlocal
py -m pip install --upgrade pip
py -m pip install -r windows-requirements.txt
py -m PyInstaller --noconfirm --clean --windowed --onefile --name "Harmonics Analysis" --hidden-import sounddevice --collect-binaries sounddevice --add-binary "ffmpeg.exe;." harmonic-viewer.py
py -m PyInstaller --noconfirm --clean --console --onefile --name "Harmonics Analysis MCP" --add-data "harmonic-viewer.py;." --collect-submodules tkinter --hidden-import cmath --hidden-import dataclasses --hidden-import hashlib --hidden-import json --hidden-import math --hidden-import queue --hidden-import shutil --hidden-import struct --hidden-import subprocess --hidden-import tempfile --hidden-import threading --hidden-import time --hidden-import urllib.request --hidden-import wave --hidden-import zipfile --hidden-import xml.etree.ElementTree harmonics-mcp.py
