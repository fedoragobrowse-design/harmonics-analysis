# Architecture

The whole application is one dependency-free file, `harmonic-viewer.py`
(Python standard library + Tk). It runs two threads on top of the Tk main
loop: a capture thread that produces analysis frames, and the UI thread that
consumes them.

```
arecord (ALSA) ──raw S16LE 48 kHz mono──▶ capture thread ──SpectrumFrame──▶ queue ──▶ UI thread
sounddevice (Windows) ──────────────────────┘                                    │  │
ffmpeg (file decode) ───────────────────────┘                                    │  ├─▶ draw_spectrum / draw_voice_profile
                                                                                 │  └─▶ active_take.add_frame (while recording)
```

## Signal pipeline

`analyze(raw: bytes) -> SpectrumFrame` is the core function:

| Step | Code | Detail |
|------|------|--------|
| Windowing | Hann, `0.5 − 0.5·cos(2πi/(N−1))` | precomputed once, then applied before each FFT |
| Transform | `fft()` | iterative radix-2, in-place bit-reversal; `FRAME_SIZE` is a power of two |
| Levels | dBFS per bin | `20·log10(magnitude·2/N / 32768)`, clamped at −80 dB floor |
| RMS | `rms_db` | frame loudness used for the −55 dB voiced/unvoiced threshold |
| Pitch | `estimate_pitch()` | harmonic-summation candidate scoring from 55 to 1,200 Hz, using up to six partials, then parabolic interpolation; returns a confidence score as well as the base note |

### Constants (`SAMPLE_RATE`, `FRAME_SIZE`, …)

| Constant | Value | Consequence |
|----------|-------|-------------|
| `SAMPLE_RATE` | 48 000 Hz | capture, decoding, and bin maths all share it |
| `FRAME_SIZE` | 4 096 | ≈ 85 ms per frame; bin width ≈ 11.7 Hz |
| `MAX_FREQUENCY` | 5 000 Hz | displayed/analysed range; the take accumulator is sized from it |
| `MIN_DB` | −80.0 dB | dB floor for display and gated bins |

## Noise floor and gating

`start_baseline_calibration` collects ~2 s of frames while the room is quiet
and averages them into `self.noise_floor`, a per-bin dB profile.
`gate_levels(levels, noise_floor)` replaces any bin below its floor value
with `MIN_DB`. Gating is applied both when drawing and when a take accumulates
frames, so steady background noise (fans, hum) does not create fake harmonics.
`clear_noise_baseline` resets to ungated analysis.

## Takes (`TakeAnalysis`)

While recording, every frame goes through `TakeAnalysis.add_frame`:

- frames with `rms_db < −55` break the current note run (silence boundary);
- gated levels feed per-bin `level_totals` (only counted for voiced frames);
- the detected fundamental maps to a note name (`note_for_frequency`);
  consecutive equal notes extend a `NoteRun`, a change starts a new one;
- the level at each of H1–H6 (bin `round(f·h·FRAME_SIZE/SAMPLE_RATE)`) feeds
  `harmonic_totals`/`harmonic_counts`, giving `harmonic_averages()`;
- every clear voiced pitch is also kept in a separate speech corpus. After
  enough conversational speech, its 10th/50th/90th percentiles form a
  spoken-pitch profile. It is explicitly not a gender or voice-type label;
- only a sustained, tightly clustered vowel-like pitch run contributes to a
  singing range; short speech-like pitch changes do not skew that result. The
  5th–95th percentile of sustained pitches produces the cautious observed
  range rather than classifying a person from their average pitch.

`show_take_summary` renders the finished take: note sequence with durations,
observed range, an H1–H6 bar chart, and overall voice colour
(`voice_colour` — strongest of three broad frequency bands: < 750 Hz,
< 2 800 Hz, rest).

Sound files run the identical pipeline (`analyze_sound_file`): FFmpeg decodes
to the same raw PCM format and the frames feed a fresh `TakeAnalysis`.

## Score analysis and practice tools

`load_score_file` accepts standard MIDI (`.mid`/`.midi`) and MuseScore
(`.mscz`/`.mscx`) files with only the Python standard library. MIDI parsing
tracks note-on/off pairs and ignores channel 10 percussion; MuseScore parsing
reads `<Note><pitch>` values from XML. `score_singability` compares the score's
written lowest/highest notes against a recorded voice take's observed 5th–95th
percentile. It deliberately reports missing evidence rather than inferring a
voice type. Multi-part scores may include accompaniment, so the UI calls out
that caveat.

The UI metronome is a Tk scheduled four-beat pulse (30–300 BPM) using the
platform alert sound. “Mute microphone” calls `stop_capture`, terminating the
input stream/process rather than merely hiding its display.

## Threading and the UI

- `capture_loop` (Linux) spawns `arecord -f S16_LE -r 48000 -c 1 -t raw -` and
  reads exactly `FRAME_SIZE·2` bytes per frame. On Windows,
  `capture_windows_loop` validates the selected input at 48 kHz first, then
  retries its native shared-mode rate and resamples PCM blocks to 48 kHz.
  A capture generation and per-run stop event prevent an old stream from
  leaking frames after a microphone change. File analysis runs in a worker
  thread.
- Frames cross to the UI thread through a bounded `queue.Queue`
  (`put_frame` drops the oldest frame on overflow, keeping the display live);
  `consume_frames` drains it via Tk `after` callbacks, so only the latest
  frame state touches Tk.
- `stop_capture` terminates the child process; the window close protocol
  routes through `quit_app` so the recorder never outlives the GUI.
- A capture-health timer detects a stalled source, reports a useful status,
  and makes a bounded restart attempt. It is disabled while the user has
  muted the microphone. This is especially important for
  Windows shared-mode devices that can disappear or change format mid-session.

## UI structure

`HarmonicViewer(tk.Tk)` builds its widgets in `_build_ui`: a spectrum canvas
(`draw_spectrum` — grid, dB levels, harmonic guides at multiples of the
detected fundamental), a voice-profile strip (`draw_voice_profile`), and the
control column (quiet baseline, record a take, sound file, MIDI/MuseScore,
metronome, true microphone mute, freeze, theme toggle). Theme is read from the
OS (`system_theme`: Windows registry, else `GTK_THEME`).

## Packaging

- `build-deb.sh` stages `/usr/lib/harmonics-analysis/harmonic-viewer.py`, a
  `/usr/bin/harmonics-analysis` launcher, and a `.desktop` entry; the package
  depends on `python3 (>= 3.10)`, `python3-tk`, `alsa-utils`, `ffmpeg`.
- The Windows build is a PyInstaller one-file exe with `ffmpeg.exe` added via
  `--add-binary`; `bundled_tool()` resolves FFmpeg from `sys._MEIPASS` first,
  so the released exe needs nothing installed.
- `.github/workflows/build-release.yml` builds both artefacts on `v*` tags.
- The Windows release job smoke-tests the packaged MCP executable after
  building it, then publishes SHA-256 sidecars for both unsigned executables.
  Windows may show an “Unknown publisher” warning until an OV or EV Authenticode
  certificate is available and the signing pipeline is reintroduced.
- Updates are downloaded to a temporary file, size-limited, checksum-verified,
  and atomically promoted. Windows retains a rollback copy during replacement;
  Linux stages the package in `/var/tmp` so APT's sandbox can read it.
