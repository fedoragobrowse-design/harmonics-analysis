# Harmonics Analysis

A local, live voice-harmonics viewer. Speak a steady vowel and watch the
spectrum peaks line up with harmonic guides while the app explains the base
note and overtone pattern in plain language — no voice-analysis terminology
required.

Everything runs on your machine. No account, no network connection, no
telemetry: microphone audio is analysed in memory and never leaves the
computer.

## Features

- **Live spectrum** — a rolling 0–5 kHz view of your voice, redrawn for every
  4 096-sample frame (about 12 times per second at 48 kHz).
- **Harmonic guides** — amber markers at H1–H6 of the detected base note, so
  you can see the overtone stack of the vowel you are holding.
- **Plain-language readout** — the current note (e.g. `A3`), tuning offset in
  cents, and an overall "voice colour" (warm / balanced / bright).
- **Windows-ready microphone capture** — choose the Windows default or a named
  input, with host API shown for each device; capture tries 48 kHz then the
  device's native shared-mode rate and normalizes it for the analyser.
- **Pitch confidence** — harmonic evidence ranks each live note as clear or
  tentative, rather than pretending a noisy/speech frame is a certain pitch.
- **Wide held-note tracking** — low and high sustained vowels are supported
  from 55–1,200 Hz; short speech-like changes are shown live but excluded from
  vocal-range labels.
- **Verified updates** — the optional updater checks the latest stable GitHub
  Release, verifies its SHA-256 checksum, replaces the Windows `.exe` on
  restart, or downloads the Linux `.deb` ready for a normal `apt install`.
- **Input-level meter** — immediately see whether the microphone is hearing
  silence, a comfortable signal, or a level that may clip.
- **Quiet baseline (noise calibration)** — two seconds of room silence set a
  per-bin noise floor; levels below it are gated out of the analysis.
- **Record a take** — while recording, every voiced frame is accumulated into
  a take; when you finish, a summary shows the notes you sang in sequence,
  a cautious sustained-range label (bass, baritone, tenor, alto, or soprano),
  pitch steadiness, and average energy at H1–H6.
- **Identify a sound file** — analyse any file FFmpeg can decode (WAV, MP3,
  FLAC, OGG, …) through the same pipeline and get the same take summary.
- **Freeze graph** — pause the drawing while capture continues.
- **Light and dark themes** — follows the OS preference where available.

## Requirements

- Python 3.10 or newer with Tkinter (`python3-tk` on Debian/Ubuntu)
- ALSA capture tool (`alsa-utils`) — Linux microphone capture uses `arecord`
- FFmpeg — used to decode sound files (and bundled into the Windows build)
- Windows: `sounddevice` and PyInstaller are only needed to build; the
  released `Harmonics Analysis.exe` is self-contained

## Install

### Debian / Ubuntu (from the `.deb`)

```sh
sudo apt install ./harmonics-analysis_1.5.5_all.deb
```

This installs a `harmonics-analysis` command and a desktop launcher.

### From source

```sh
sudo apt install python3-tk alsa-utils ffmpeg   # Debian/Ubuntu
./harmonic-viewer.py                            # or: python3 harmonic-viewer.py
```

### Windows

Download `Harmonics-Analysis-Windows.exe` from the latest GitHub release (built
automatically for every `v*` tag) and run it. FFmpeg is bundled inside.

## How it works (short version)

1. **Capture** — `arecord` streams raw 16-bit mono PCM at 48 kHz (Windows uses
   `sounddevice`); sound files are decoded to the same format by FFmpeg.
2. **Frame analysis** — each 4 096-sample frame is windowed, transformed with
   a radix-2 FFT, and converted to dB levels for 0–5 kHz bins.
3. **Pitch detection** — harmonic summation scores a possible base note
   against up to six overtones between 55 and 1,200 Hz, then refines it with
   parabolic interpolation. This avoids treating a loud overtone as the base
   note and covers unusually low and high held vowels.
4. **Aggregation** — voiced frames (above −55 dB RMS, above the noise floor)
   accumulate into note runs, per-bin level totals, and harmonic totals.

Details, constants, and the data flow are in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Building

- Debian package: `./build-deb.sh` → `harmonics-analysis_1.5.5_all.deb`
- Windows exe: see [`docs/BUILDING.md`](docs/BUILDING.md)
- CI: pushing a tag `v*` runs `.github/workflows/build-release.yml`, which
  builds both artefacts, SHA-256 checksum sidecars, and the GitHub Release that
  the in-app updater uses. A manual dispatch validates build artefacts only.

## Repository layout

```
harmonic-viewer.py      the entire application (single file, stdlib only)
build-deb.sh            builds the Debian package
build-windows.bat       Windows PyInstaller build (local)
windows-requirements.txt  build-time deps for Windows (PyInstaller, sounddevice)
.github/workflows/      release CI (Windows exe + Debian .deb)
release-artifacts/      output of the last release build (not tracked)
```

## MCP server

`harmonics-mcp.py` is a local stdio MCP server. It exposes version/settings and
analysis of one client-supplied 48 kHz PCM frame; it never opens the microphone,
watches files, uploads audio, or retains input. See [`docs/MCP.md`](docs/MCP.md)
for configuration.

## Privacy

The application performs all analysis locally. It opens no network
connections; microphone and file audio are processed in memory only, and
takes are shown in a summary window without being written to disk.
