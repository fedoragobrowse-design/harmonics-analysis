# Building and releasing

## Debian package

```sh
./build-deb.sh
# → harmonics-analysis_1.0.0_all.deb
sudo apt install ./harmonics-analysis_1.0.0_all.deb
```

The script stages a `build/` tree with:

- `/usr/lib/harmonics-analysis/harmonic-viewer.py` — the application
- `/usr/bin/harmonics-analysis` — launcher (`python3` + the script)
- `/usr/share/applications/harmonics-analysis.desktop` — menu entry

Declared dependencies: `python3 (>= 3.10)`, `python3-tk`, `alsa-utils`,
`ffmpeg`. Bump `version=` at the top of `build-deb.sh` and the `Version:`
field in its control heredoc together when releasing.

## Windows exe

Locally (needs Python + PyInstaller + FFmpeg on PATH):

```bat
build-windows.bat
```

which runs `pyinstaller --windowed --onefile --name "Harmonics Analysis"
--add-binary "ffmpeg.exe;." harmonic-viewer.py`. `bundled_tool("ffmpeg")`
resolves the bundled copy from `sys._MEIPASS` at runtime, so the exe is
self-contained.

Build-time requirements live in `windows-requirements.txt`
(`pyinstaller`, `sounddevice`).

## CI release

`.github/workflows/build-release.yml` triggers on:

- pushing a tag matching `v*`
- manual `workflow_dispatch`

It builds on both platforms in parallel and uploads the artefacts
(`Harmonics-Analysis-Windows` / `Harmonics-Analysis-Debian`). Release steps:

```sh
git tag v1.0.1 && git push origin v1.0.1
```

then attach the downloaded artefacts to a GitHub release (`gh release create`).

## Notes

- `release-artifacts/` holds the output of the last local/CI build and is not
  tracked by git.
- `build/`, `dist/`, `__pycache__/`, `*.spec`, `*.deb` are gitignored.
