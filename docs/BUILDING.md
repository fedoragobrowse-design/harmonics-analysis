# Building and releasing

## Debian package

```sh
./build-deb.sh
# → harmonics-analysis_1.5.0_all.deb
sudo apt install ./harmonics-analysis_1.5.0_all.deb
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
--hidden-import sounddevice --collect-binaries sounddevice --add-binary
"ffmpeg.exe;." harmonic-viewer.py`. The explicit sounddevice collection keeps
the Windows PortAudio capture backend in the executable. `bundled_tool("ffmpeg")`
resolves the bundled copy from `sys._MEIPASS` at runtime, so the exe is
self-contained.

Build-time requirements live in `windows-requirements.txt`
(`pyinstaller`, `sounddevice`).

## CI release

`.github/workflows/build-release.yml` triggers on:

- pushing a tag matching `v*`
- manual `workflow_dispatch`

It builds on both platforms in parallel and uploads the artefacts
(`Harmonics-Analysis-Windows` / `Harmonics-Analysis-Debian`). A `v*` tag also
creates the GitHub Release with both asset files and SHA-256 sidecars; those
are the only files accepted by the in-app updater. Release steps:

```sh
git tag v1.5.0 && git push origin v1.5.0
```

then attach the downloaded artefacts to a GitHub release (`gh release create`).

## Notes

- `release-artifacts/` holds the output of the last local/CI build and is not
  tracked by git.
- `build/`, `dist/`, `__pycache__/`, `*.spec`, `*.deb` are gitignored.
