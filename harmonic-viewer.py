#!/usr/bin/env python3
"""Local, live voice-harmonics viewer.

Speak a steady vowel and watch the cyan spectrum peaks line up with amber
harmonic guides.  The viewer explains the base note and overtone pattern in
plain language; no voice-analysis terminology is required.
"""

from __future__ import annotations

import cmath
import hashlib
import json
import math
import os
import queue
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.request
from dataclasses import dataclass, field
from tkinter import filedialog, messagebox


SAMPLE_RATE = 48_000
CHANNELS = 1
FRAME_SIZE = 4_096
MAX_FREQUENCY = 5_000
MIN_DB = -80.0
MAX_DB = 0.0
CANVAS_WIDTH = 670
SPECTRUM_HEIGHT = 350
MIN_PITCH_HZ = 55.0
MAX_PITCH_HZ = 1_200.0
VOICE_RMS_DB = -55.0
RANGE_HOLD_FRAMES = 9  # about 0.77 seconds at the current frame size
RANGE_STABILITY_CENTS = 35.0
APP_VERSION = "1.6.4"
RELEASES_API = "https://api.github.com/repos/fedoragobrowse-design/harmonics-analysis/releases/latest"
MAX_BIN = min(FRAME_SIZE // 2, int(MAX_FREQUENCY * FRAME_SIZE / SAMPLE_RATE))
HANN_WINDOW = tuple(0.5 - 0.5 * math.cos(2.0 * math.pi * index / (FRAME_SIZE - 1)) for index in range(FRAME_SIZE))
MAX_UPDATE_BYTES = 200 * 1024 * 1024


def system_theme() -> str:
    """Use the OS preference when it is exposed; otherwise start in dark mode."""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return "light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "dark"
        except OSError:
            pass
    gtk_theme = os.environ.get("GTK_THEME", "").lower()
    if "dark" in gtk_theme:
        return "dark"
    if gtk_theme:
        return "light"
    return "dark"

def bundled_tool(name: str) -> str:
    """Locate a PyInstaller-bundled helper before falling back to PATH."""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        candidate = os.path.join(bundle_dir, name)
        if os.path.isfile(candidate):
            return candidate
    return name


def about_text() -> str:
    """Keep the About page factual and available without a network request."""
    return (
        f"Harmonics Analysis v{APP_VERSION}\n\n"
        "A local visual tuner for voice and instruments. Pitch, harmonics, "
        "and recording summaries are analysed on this device; microphone "
        "audio is never uploaded or retained.\n\n"
        "Voice mode reports observed registers cautiously. Instrument mode "
        "never applies a vocal classification.\n\n"
        "Updates come from GitHub Releases, require a SHA-256 match, and use "
        "safe platform installers. The optional MCP server accepts only audio "
        "frames supplied by its client and does not open microphones or watch files."
    )


def resample_pcm_16le(raw: bytes, source_rate: float) -> bytes:
    """Convert one mono PCM block to the analysis rate without extra packages."""
    if round(source_rate) == SAMPLE_RATE:
        return raw
    samples = struct.unpack(f"<{len(raw) // 2}h", raw)
    output_count = max(1, round(len(samples) * SAMPLE_RATE / source_rate))
    converted: list[int] = []
    for output_index in range(output_count):
        source_position = output_index * source_rate / SAMPLE_RATE
        left = min(int(source_position), len(samples) - 1)
        right = min(left + 1, len(samples) - 1)
        fraction = source_position - left
        converted.append(round(samples[left] + (samples[right] - samples[left]) * fraction))
    return struct.pack(f"<{len(converted)}h", *converted)


def windows_capture_candidates(sound: object, device_index: int | None) -> list[float]:
    """Try 48 kHz first, then the selected device's shared-mode rate."""
    device = sound.query_devices(device_index, "input")
    native_rate = float(device["default_samplerate"])
    rates = (float(SAMPLE_RATE), native_rate)
    return list(dict.fromkeys(rate for rate in rates if math.isfinite(rate) and rate > 0))


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    asset_url: str
    checksum_url: str


@dataclass
class PitchLock:
    """Small median filter that steadies the display without hiding a leap."""
    values: list[float] = field(default_factory=list)

    def update(self, frequency: float | None, confidence: float) -> float | None:
        if frequency is None or confidence < 0.42:
            self.values.clear()
            return None
        if self.values and abs(1200 * math.log2(frequency / self.values[-1])) > 350:
            self.values.clear()
        self.values.append(frequency)
        del self.values[:-5]
        return sorted(self.values)[len(self.values) // 2]


def version_key(version: str) -> tuple[int, ...] | None:
    """Parse stable vX.Y.Z-style tags without accepting prereleases."""
    text = version.removeprefix("v")
    if not text or any(not part.isdigit() for part in text.split(".")):
        return None
    return tuple(int(part) for part in text.split("."))


def select_update(release: dict[str, object], current_version: str, windows: bool) -> UpdateInfo | None:
    """Select a stable release asset only when it is newer than this app."""
    tag = str(release.get("tag_name", ""))
    candidate, current = version_key(tag), version_key(current_version)
    if release.get("prerelease") or release.get("draft") or candidate is None or current is None or candidate <= current:
        return None
    assets = release.get("assets")
    if not isinstance(assets, list):
        return None
    suffix = "Harmonics-Analysis-Windows.exe" if windows else "_all.deb"
    selected = next((asset for asset in assets if isinstance(asset, dict) and str(asset.get("name", "")).endswith(suffix) and (windows or str(asset.get("name", "")).startswith("harmonics-analysis_"))), None)
    if selected is None:
        return None
    name = str(selected.get("name", ""))
    checksum = next((asset for asset in assets if isinstance(asset, dict) and str(asset.get("name", "")) == f"{name}.sha256"), None)
    if checksum is None:
        return None
    asset_url, checksum_url = selected.get("browser_download_url"), checksum.get("browser_download_url")
    if not isinstance(asset_url, str) or not isinstance(checksum_url, str):
        return None
    return UpdateInfo(tag.removeprefix("v"), asset_url, checksum_url)


def fetch_update_info(windows: bool, opener: object = urllib.request.urlopen) -> UpdateInfo | None:
    request = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json", "User-Agent": f"Harmonics-Analysis/{APP_VERSION}"})
    with opener(request, timeout=10) as response:
        release = json.loads(response.read().decode("utf-8"))
    if not isinstance(release, dict):
        return None
    return select_update(release, APP_VERSION, windows)


def sha256_from_sidecar(data: bytes) -> str | None:
    value = data.decode("ascii", errors="ignore").strip().split()
    if not value or len(value[0]) != 64 or any(character not in "0123456789abcdefABCDEF" for character in value[0]):
        return None
    return value[0].lower()


def download_verified(url: str, checksum_url: str, destination: str, opener: object = urllib.request.urlopen) -> None:
    """Download an update atomically and verify its published SHA-256 first."""
    with opener(checksum_url, timeout=15) as response:
        expected = sha256_from_sidecar(response.read())
    if expected is None:
        raise RuntimeError("The release checksum is missing or invalid.")
    directory = os.path.dirname(destination) or "."
    os.makedirs(directory, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".harmonics-update-", dir=directory)
    try:
        with os.fdopen(handle, "wb") as output, opener(url, timeout=30) as response:
            downloaded = 0
            while chunk := response.read(1_048_576):
                downloaded += len(chunk)
                if downloaded > MAX_UPDATE_BYTES:
                    raise RuntimeError("The update is larger than the 200 MiB safety limit.")
                output.write(chunk)
        with open(temporary, "rb") as output:
            actual = hashlib.file_digest(output, "sha256").hexdigest()
        if actual != expected:
            raise RuntimeError("The downloaded update did not match its SHA-256 checksum.")
        os.replace(temporary, destination)
        # APT's unprivileged _apt user must be able to read a local package.
        # Keeping the file in a public temporary directory avoids an unsafe
        # root fallback caused by private home-directory permissions.
        os.chmod(destination, 0o644)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def linux_update_command(package_path: str) -> list[str]:
    """Build a deliberately constrained, Polkit-authorized package install."""
    absolute_path = os.path.abspath(package_path)
    filename = os.path.basename(absolute_path)
    if not filename.startswith("harmonics-analysis_") or not filename.endswith("_all.deb"):
        raise RuntimeError("Refusing to install an unexpected package filename.")
    polkit = shutil.which("pkexec")
    apt_get = shutil.which("apt-get")
    if not polkit or not apt_get:
        raise RuntimeError("Automatic updates need Polkit and apt-get on this Linux system.")
    # No upgrades, no downloads, and no package removals.  If dependency
    # resolution would alter anything else, APT aborts and the old app stays.
    return [polkit, apt_get, "install", "-y", "--no-remove", "--no-download", absolute_path]


def linux_update_staging_directory() -> str:
    """Prefer a directory traversable by APT's sandboxed _apt user."""
    for directory in ("/var/tmp", tempfile.gettempdir()):
        if os.path.isdir(directory) and os.access(directory, os.W_OK | os.X_OK):
            return directory
    raise RuntimeError("No safe temporary directory is available for the update package.")

@dataclass(frozen=True)
class SpectrumFrame:
    levels: tuple[float, ...]
    rms_db: float
    fundamental_hz: float | None
    pitch_confidence: float = 0.0


@dataclass
class NoteRun:
    name: str
    frames: int


@dataclass
class TakeAnalysis:
    level_totals: list[float]
    frame_count: int = 0
    voiced_frame_count: int = 0
    note_runs: list[NoteRun] = field(default_factory=list)
    last_note: str | None = None
    harmonic_totals: list[float] = field(default_factory=lambda: [0.0] * 6)
    harmonic_counts: list[int] = field(default_factory=lambda: [0] * 6)
    frequency_total: float = 0.0
    frequency_square_total: float = 0.0
    lowest_frequency: float | None = None
    highest_frequency: float | None = None
    sustained_frequencies: list[float] = field(default_factory=list)
    _stable_run: list[float] = field(default_factory=list)

    @classmethod
    def start(cls, level_count: int) -> "TakeAnalysis":
        return cls([0.0] * level_count)

    def average_levels(self) -> tuple[float, ...]:
        if not self.voiced_frame_count:
            return tuple(MIN_DB for _ in self.level_totals)
        return tuple(level / self.voiced_frame_count for level in self.level_totals)

    def harmonic_averages(self) -> tuple[float | None, ...]:
        return tuple(
            total / count if count else None
            for total, count in zip(self.harmonic_totals, self.harmonic_counts)
        )

    def add_frame(self, frame: SpectrumFrame, noise_floor: tuple[float, ...] | None) -> None:
        self.frame_count += 1
        if frame.rms_db < VOICE_RMS_DB or frame.fundamental_hz is None or frame.pitch_confidence < 0.42:
            self.last_note = None
            self._stable_run.clear()
            return
        levels = gate_levels(frame.levels, noise_floor)
        fundamental = detect_fundamental(levels)
        if fundamental is None:
            self.last_note = None
            self._stable_run.clear()
            return

        self.voiced_frame_count += 1
        self.frequency_total += fundamental
        self.frequency_square_total += fundamental * fundamental
        self.lowest_frequency = fundamental if self.lowest_frequency is None else min(self.lowest_frequency, fundamental)
        self.highest_frequency = fundamental if self.highest_frequency is None else max(self.highest_frequency, fundamental)
        for index, level in enumerate(levels):
            self.level_totals[index] += level
        note = note_for_frequency(fundamental)
        if note == self.last_note:
            self.note_runs[-1].frames += 1
        else:
            self.note_runs.append(NoteRun(note, 1))
            self.last_note = note
        if self._stable_run and abs(1200 * math.log2(fundamental / self._stable_run[-1])) <= RANGE_STABILITY_CENTS:
            self._stable_run.append(fundamental)
        else:
            self._stable_run = [fundamental]
        # Speech can have a detectable pitch, but it typically moves more
        # than 35 cents from frame to frame. Only a ~0.8 second, tightly held
        # vowel-like note enters a range result; live note display is unchanged.
        if len(self._stable_run) >= RANGE_HOLD_FRAMES:
            self.sustained_frequencies.append(fundamental)
        for harmonic in range(1, 7):
            bin_index = round(fundamental * harmonic * FRAME_SIZE / SAMPLE_RATE)
            if bin_index >= len(levels):
                break
            self.harmonic_totals[harmonic - 1] += levels[bin_index]
            self.harmonic_counts[harmonic - 1] += 1

    def pitch_stability(self) -> str:
        """Return a friendly steadiness description for the recorded pitch."""
        if self.voiced_frame_count < 2:
            return "Not enough steady pitch to rate"
        average = self.frequency_total / self.voiced_frame_count
        variance = max(0.0, self.frequency_square_total / self.voiced_frame_count - average * average)
        spread_cents = 1200 * math.log2((average + math.sqrt(variance)) / average) if variance else 0.0
        if spread_cents < 15:
            return "Very steady"
        if spread_cents < 35:
            return "Mostly steady"
        return "Expressive / changing pitch"


def fft(values: list[complex]) -> list[complex]:
    """Iterative radix-2 FFT. FRAME_SIZE is intentionally a power of two."""
    count = len(values)
    output = values[:]

    index = 0
    for position in range(1, count):
        bit = count >> 1
        while index & bit:
            index ^= bit
            bit >>= 1
        index ^= bit
        if position < index:
            output[position], output[index] = output[index], output[position]

    size = 2
    while size <= count:
        phase = -2.0 * math.pi / size
        step = complex(math.cos(phase), math.sin(phase))
        half = size // 2
        for start in range(0, count, size):
            twiddle = 1.0 + 0.0j
            for offset in range(half):
                even = output[start + offset]
                odd = twiddle * output[start + offset + half]
                output[start + offset] = even + odd
                output[start + offset + half] = even - odd
                twiddle *= step
        size <<= 1

    return output


def analyze(raw: bytes) -> SpectrumFrame:
    if len(raw) != FRAME_SIZE * 2:
        raise ValueError(f"Expected {FRAME_SIZE * 2} bytes of 16-bit PCM, got {len(raw)}.")
    samples = struct.unpack(f"<{FRAME_SIZE}h", raw)
    rms = math.sqrt(sum(sample * sample for sample in samples) / FRAME_SIZE)
    rms_db = 20.0 * math.log10(max(rms / 32768.0, 1e-8))

    # Precomputing the Hann window removes 4,096 cosine calls per live frame.
    windowed = [complex(sample * HANN_WINDOW[index], 0.0) for index, sample in enumerate(samples)]
    transform = fft(windowed)
    levels = []
    for index in range(MAX_BIN + 1):
        magnitude = abs(transform[index]) * 2.0 / FRAME_SIZE
        levels.append(20.0 * math.log10(max(magnitude / 32768.0, 1e-8)))

    fundamental, confidence = estimate_pitch(levels)
    return SpectrumFrame(tuple(levels), rms_db, fundamental, confidence)

def _peak_level(levels: tuple[float, ...] | list[float], bin_position: float) -> tuple[float, float]:
    """Return the strongest nearby bin and a sub-bin position."""
    centre = round(bin_position)
    if centre < 2 or centre >= len(levels) - 2:
        return MIN_DB, float(centre)
    strongest = max(range(centre - 1, centre + 2), key=levels.__getitem__)
    left, middle, right = levels[strongest - 1], levels[strongest], levels[strongest + 1]
    curve = left - 2.0 * middle + right
    offset = 0.0 if curve == 0.0 else max(-0.5, min(0.5, 0.5 * (left - right) / curve))
    return middle, strongest + offset


def estimate_pitch(levels: tuple[float, ...] | list[float]) -> tuple[float | None, float]:
    """Harmonic-summation F0 estimator, resilient to a weak fundamental.

    Selecting the loudest FFT peak mistakes an overtone for the singer's base
    note.  Here each possible base note is scored against up to six of its
    harmonics; this handles both low notes and very high voices.
    """
    low_bin = max(2, math.ceil(MIN_PITCH_HZ * FRAME_SIZE / SAMPLE_RATE))
    high_bin = min(len(levels) - 2, math.floor(MAX_PITCH_HZ * FRAME_SIZE / SAMPLE_RATE))
    candidates: list[tuple[float, int, int, float]] = []
    for base_bin in range(low_bin, high_bin + 1):
        values: list[float] = []
        positions: list[float] = []
        for harmonic in range(1, 7):
            harmonic_bin = base_bin * harmonic
            if harmonic_bin >= len(levels) - 2:
                break
            level, position = _peak_level(levels, harmonic_bin)
            values.append(level)
            positions.append(position)
        audible = sum(level > -58.0 for level in values)
        if audible < 2:
            continue
        # dB above the display floor, with a modest preference for a real H1.
        score = sum(max(0.0, level - MIN_DB) for level in values) / len(values)
        score += max(0.0, values[0] - MIN_DB) * 0.18
        candidates.append((score, base_bin, audible, positions[0]))
    if not candidates:
        return None, 0.0
    candidates.sort(reverse=True)
    score, base_bin, audible, first_position = candidates[0]
    runner_up = candidates[1][0] if len(candidates) > 1 else MIN_DB
    confidence = min(1.0, 0.30 + 0.11 * audible + max(0.0, score - runner_up) / 55.0)
    if score < 17.0 or confidence < 0.42:
        return None, confidence
    # H1 gives the most accurate interpolation when present; otherwise the
    # harmonic grid still provides a stable base-bin estimate.
    position = first_position if levels[round(first_position)] > -58.0 else float(base_bin)
    return position * SAMPLE_RATE / FRAME_SIZE, confidence


def detect_fundamental(levels: tuple[float, ...] | list[float]) -> float | None:
    """Compatibility wrapper used after noise gating."""
    return estimate_pitch(levels)[0]


def note_for_frequency(frequency_hz: float) -> str:
    """Return the nearest equal-temperament note for a friendly status label."""
    midi = round(69 + 12 * math.log2(frequency_hz / 440.0))
    names = ("C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B")
    return f"{names[midi % 12]}{midi // 12 - 1}"


def tuning_for_frequency(frequency_hz: float) -> str:
    """Describe the pitch offset from the nearest equal-temperament note."""
    exact_midi = 69 + 12 * math.log2(frequency_hz / 440.0)
    cents = round((exact_midi - round(exact_midi)) * 100)
    if abs(cents) <= 5:
        return "In tune"
    direction = "sharp" if cents > 0 else "flat"
    return f"{abs(cents)} cents {direction}"


def harmonic_note_stack(fundamental_hz: float, count: int = 6) -> str:
    """Name the nearest equal-temperament note at each natural harmonic."""
    return "  ·  ".join(f"H{number} {note_for_frequency(fundamental_hz * number)}" for number in range(1, count + 1))

def gate_levels(levels: tuple[float, ...], noise_floor: tuple[float, ...] | None) -> tuple[float, ...]:
    if noise_floor is None:
        return levels
    return tuple(value if value > floor else MIN_DB for value, floor in zip(levels, noise_floor))


def voice_colour(levels: tuple[float, ...]) -> str:
    groups = [0.0, 0.0, 0.0]
    counts = [0, 0, 0]
    for index, level in enumerate(levels):
        frequency = index * SAMPLE_RATE / FRAME_SIZE
        group = 0 if frequency < 750 else 1 if frequency < 2_800 else 2
        groups[group] += level
        counts[group] += 1
    strongest = max(range(3), key=lambda index: groups[index] / max(counts[index], 1))
    return (
        "Warm / lower-frequency weighted",
        "Balanced / midrange weighted",
        "Bright / upper-frequency weighted",
    )[strongest]


def vocal_range_label(take: TakeAnalysis) -> tuple[str, str]:
    """Report an observed register without diagnosing a singer from a short take."""
    values = sorted(take.sustained_frequencies)
    if len(values) < 3:
        return "No held vowel yet", "Live notes work for speech, but range results require one calm vowel held for about a second."
    low_frequency = values[max(0, round((len(values) - 1) * 0.05))]
    high_frequency = values[min(len(values) - 1, round((len(values) - 1) * 0.95))]
    low = 69 + 12 * math.log2(low_frequency / 440.0)
    high = 69 + 12 * math.log2(high_frequency / 440.0)
    if high - low < 5 or len(values) < 10:
        midpoint = (low + high) / 2
        register = "low" if midpoint < 53 else "low-mid" if midpoint < 60 else "mid" if midpoint < 67 else "upper-mid" if midpoint < 74 else "high"
        return f"{register.capitalize()} observed register", f"Observed: {note_for_frequency(low_frequency)} to {note_for_frequency(high_frequency)}. A longer take can refine this, but no voice type is assigned from a short sample."
    profiles = (
        ("Lower register profile", 40, 64),
        ("Low-mid register profile", 43, 67),
        ("Mid register profile", 48, 72),
        ("Mid-high register profile", 53, 77),
        ("High register profile", 60, 84),
    )
    # Choose the common range with the largest overlap; centres break a tie.
    def score(profile: tuple[str, int, int]) -> tuple[float, float]:
        _name, bottom, top = profile
        overlap = max(0.0, min(high, top) - max(low, bottom))
        return overlap, -abs((low + high) / 2 - (bottom + top) / 2)
    name, _bottom, _top = max(profiles, key=score)
    return name, f"Sustained range: {note_for_frequency(low_frequency)} to {note_for_frequency(high_frequency)}. This describes the sample, not a gender or a fixed voice diagnosis."


def instrument_range_label(take: TakeAnalysis) -> tuple[str, str]:
    """Describe notes without applying human voice categories to an instrument."""
    values = sorted(take.sustained_frequencies)
    if len(values) < 3:
        return "Instrument pitch", "Hold a note briefly to show its detected pitch."
    low = values[max(0, round((len(values) - 1) * 0.05))]
    high = values[min(len(values) - 1, round((len(values) - 1) * 0.95))]
    return "Instrument range", f"Observed notes: {note_for_frequency(low)} to {note_for_frequency(high)}. No vocal classification is applied."


class HarmonicViewer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Harmonics analysis")
        self.theme_mode = system_theme()
        self.configure(bg="#17222e")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.quit_app)

        self.stop_event = threading.Event()
        self.frames: queue.Queue[SpectrumFrame | Exception] = queue.Queue(maxsize=2)
        self.process: subprocess.Popen[bytes] | None = None
        self.input_stream: object | None = None
        self.worker: threading.Thread | None = None
        self.running = False
        self.last_frame_at = time.monotonic()
        self.noise_floor: tuple[float, ...] | None = None
        self.frozen = False
        self.latest_frame: SpectrumFrame | None = None
        self.baseline_samples: list[tuple[float, ...]] = []
        self.calibrating = False
        self.baseline_frame_count = 24
        self.recording = False
        self.active_take: TakeAnalysis | None = None
        self.file_analysis_active = False
        self.input_device: int | None = None
        self.capture_generation = 0
        self.capture_rate = float(SAMPLE_RATE)
        self.windows_overflow_count = 0
        self.pitch_lock = PitchLock()
        self.recent_note_names: list[str] = []
        self.mic_retries = 0
        self.analysis_mode = "Voice"

        self.status = tk.StringVar(value="Getting microphone ready…")
        self.recording_status = tk.StringVar(value="")
        self.note_display = tk.StringVar(value="—")
        self.base_note = tk.StringVar(value="Hold a steady “aaa” to name your note")
        self.tuning = tk.StringVar(value="")
        self.recipe = tk.StringVar(value="Waiting for a harmonic stack")
        self.noise_filter = tk.StringVar(value="Noise filter is off")
        self.detail = tk.StringVar(value="Cyan is what your microphone hears. Amber marks the harmonic pattern.")
        self.voice_quality = tk.StringVar(value="Listening for your voice colour")
        self.input_level = tk.StringVar(value="Input level: waiting for microphone")
        self.note_trail = tk.StringVar(value="Note trail: waiting")
        self.range_coach = tk.StringVar(value="Range coach: hold one comfortable vowel")

        self._build_ui()
        self._draw_grid()
        self.start_capture()
        self.after(35, self.consume_frames)
        self.after(1_000, self.monitor_capture_health)

    def _build_ui(self) -> None:
        if self.theme_mode == "light":
            studio = "#e6ece9"
            paper = "#fff8e9"
            ink = "#192632"
            muted_ink = "#586b75"
            amber = "#a66119"
            line = "#d1ae72"
            header_text = "#192632"
            header_muted = "#52656f"
            self.scope_background = "#f8fbf8"
            self.scope_base_zone = "#e5f0ed"
            self.scope_grid = "#c8d9d5"
            self.scope_text = "#526b73"
            self.scope_trace = "#187e98"
            self.scope_harmonics = "#b86f20"
        else:
            studio = "#17222e"
            paper = "#223445"
            ink = "#f2eee6"
            muted_ink = "#b2c0c6"
            amber = "#e1a347"
            line = "#527085"
            header_text = "#f6f2e9"
            header_muted = "#aebec8"
            self.scope_background = "#0b141c"
            self.scope_base_zone = "#0b1b29"
            self.scope_grid = "#1b303c"
            self.scope_text = "#668da3"
            self.scope_trace = "#58d7ff"
            self.scope_harmonics = "#f6b73c"
        self.configure(bg=studio)
        self.studio = studio
        self.paper = paper
        self.ink = ink
        self.muted_ink = muted_ink
        self.line = line

        frame = tk.Frame(self, bg=studio, padx=22, pady=20)
        frame.pack(fill="both", expand=True)
        header = tk.Frame(frame, bg=studio)
        header.pack(fill="x", pady=(0, 16))
        self.theme_button = tk.Button(
            header,
            text="Use dark mode" if self.theme_mode == "light" else "Use light mode",
            command=self.toggle_theme,
            bg=paper,
            fg=ink,
            activebackground=line,
            activeforeground=ink,
            relief="flat",
            padx=12,
            pady=6,
        )
        self.theme_button.pack(side="right")
        tk.Label(
            header,
            text="Harmonics analysis",
            fg=header_text,
            bg=studio,
            font=("Sans", 22, "bold"),
        ).pack(anchor="w")
        tk.Label(
            header,
            text="A live sketch of the note, colour, and harmonic shape in your voice.",
            fg=header_muted,
            bg=studio,
            font=("Sans", 10),
        ).pack(anchor="w", pady=(2, 0))

        content = tk.Frame(frame, bg=studio)
        content.pack(fill="both", expand=True)

        note_panel = tk.Frame(
            content,
            width=310,
            bg=paper,
            padx=18,
            pady=18,
            highlightthickness=2,
            highlightbackground=line,
        )
        note_panel.pack(side="left", fill="y")
        tk.Label(note_panel, text="You are singing", fg=muted_ink, bg=paper, font=("Sans", 10, "bold")).pack(anchor="w")
        tk.Label(note_panel, textvariable=self.note_display, fg=amber, bg=paper, font=("Sans", 46, "bold")).pack(anchor="w", pady=(1, 0))
        tk.Label(note_panel, textvariable=self.base_note, fg=ink, bg=paper, font=("Sans", 10), wraplength=270, justify="left").pack(anchor="w")
        tk.Label(note_panel, textvariable=self.tuning, fg="#257981", bg=paper, font=("Sans", 9, "bold")).pack(anchor="w", pady=(3, 0))
        tk.Label(note_panel, textvariable=self.note_trail, fg=muted_ink, bg=paper, font=("Sans", 8), wraplength=270, justify="left").pack(anchor="w", pady=(4, 0))
        tk.Frame(note_panel, height=2, bg=line).pack(fill="x", pady=15)
        tk.Label(note_panel, text="Harmonic family", fg=muted_ink, bg=paper, font=("Sans", 10, "bold")).pack(anchor="w")
        harmonic_text = tk.Frame(note_panel, height=44, bg=paper)
        harmonic_text.pack(fill="x", pady=(4, 0))
        harmonic_text.pack_propagate(False)
        tk.Label(harmonic_text, textvariable=self.recipe, fg=ink, bg=paper, font=("Sans", 10), wraplength=270, justify="left").pack(anchor="w")
        tk.Label(
            note_panel,
            text="One held note naturally produces this related stack. It is not automatically a chord.",
            fg=muted_ink,
            bg=paper,
            font=("Sans", 9),
            wraplength=270,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))
        tk.Frame(note_panel, height=2, bg=line).pack(fill="x", pady=15)
        tk.Label(note_panel, text="Voice colour", fg=muted_ink, bg=paper, font=("Sans", 10, "bold")).pack(anchor="w")
        tk.Label(
            note_panel,
            text="Where your sound carries its energy",
            fg=muted_ink,
            bg=paper,
            font=("Sans", 9),
        ).pack(anchor="w", pady=(3, 6))
        self.voice_canvas = tk.Canvas(
            note_panel,
            width=270,
            height=88,
            bg="#12232b",
            highlightthickness=0,
        )
        self.voice_canvas.pack(anchor="w")
        voice_scale = tk.Frame(note_panel, bg=paper)
        voice_scale.pack(fill="x", pady=(5, 0))
        tk.Label(voice_scale, text="Warm", fg="#178f8b", bg=paper, font=("Sans", 8, "bold")).pack(side="left")
        tk.Label(voice_scale, text="Balanced", fg=amber, bg=paper, font=("Sans", 8, "bold")).pack(side="left", expand=True)
        tk.Label(voice_scale, text="Bright", fg="#c0527b", bg=paper, font=("Sans", 8, "bold")).pack(side="right")
        tk.Label(note_panel, textvariable=self.voice_quality, fg=ink, bg=paper, font=("Sans", 9), wraplength=270, justify="left").pack(anchor="w", pady=(5, 0))
        tk.Label(note_panel, textvariable=self.range_coach, fg="#37644a", bg=paper, font=("Sans", 8, "bold"), wraplength=270, justify="left").pack(anchor="w", pady=(5, 0))
        tk.Frame(note_panel, height=2, bg=line).pack(fill="x", pady=15)
        tk.Label(note_panel, text="Quiet-room filter", fg=muted_ink, bg=paper, font=("Sans", 10, "bold")).pack(anchor="w")
        tk.Label(note_panel, textvariable=self.noise_filter, fg="#37644a", bg=paper, font=("Sans", 9), wraplength=270, justify="left").pack(anchor="w", pady=(4, 8))
        tk.Button(
            note_panel,
            text="Set quiet baseline",
            command=self.start_baseline_calibration,
            bg="#386b4b",
            fg="#ffffff",
            activebackground="#4b8d63",
            activeforeground="#ffffff",
            relief="flat",
            padx=12,
            pady=7,
        ).pack(anchor="w")
        tk.Button(
            note_panel,
            text="Clear baseline",
            command=self.clear_noise_baseline,
            bg="#d8cab0",
            fg=ink,
            activebackground="#c8b893",
            activeforeground=ink,
            relief="flat",
            padx=12,
            pady=7,
        ).pack(anchor="w", pady=(7, 0))

        graph_panel = tk.Frame(content, bg=studio)
        graph_panel.pack(side="left", fill="both", expand=True, padx=(18, 0))
        tk.Label(graph_panel, text="Live spectrum", fg="#d9b36b", bg=studio, font=("Sans", 14, "bold")).pack(anchor="w")
        tk.Label(graph_panel, textvariable=self.status, fg="#76dfe0", bg=studio, font=("Sans", 10, "bold"), wraplength=CANVAS_WIDTH, justify="left").pack(anchor="w", pady=(3, 2))
        tk.Label(graph_panel, textvariable=self.recording_status, fg="#f1a3a3", bg=studio, font=("Sans", 9, "bold"), wraplength=CANVAS_WIDTH, justify="left").pack(anchor="w", pady=(0, 7))
        self.level_canvas = tk.Canvas(graph_panel, width=CANVAS_WIDTH, height=18, bg=self.scope_background, highlightthickness=0)
        self.level_canvas.pack(anchor="w", pady=(0, 5))
        tk.Label(graph_panel, textvariable=self.input_level, fg=header_muted, bg=studio, font=("Sans", 8, "bold")).pack(anchor="w", pady=(0, 5))
        self.canvas = tk.Canvas(
            graph_panel,
            width=CANVAS_WIDTH,
            height=SPECTRUM_HEIGHT,
            bg=self.scope_background,
            highlightthickness=1,
            highlightbackground=self.scope_grid,
        )
        self.canvas.pack()
        tk.Label(
            graph_panel,
            text="Cyan is the sound arriving at the microphone. Amber guides are the harmonic positions for the note at left.",
            fg="#afc1ca",
            bg=studio,
            font=("Sans", 9),
            wraplength=CANVAS_WIDTH,
            justify="left",
        ).pack(anchor="w", pady=(9, 0))
        tk.Label(graph_panel, textvariable=self.detail, fg="#93abb8", bg=studio, font=("Sans", 9), wraplength=CANVAS_WIDTH, justify="left").pack(anchor="w", pady=(6, 0))
        controls = tk.Frame(graph_panel, bg=studio)
        controls.pack(fill="x", pady=(12, 0))
        self.record_button = tk.Button(
            controls,
            text="Record a take",
            command=self.toggle_recording,
            bg="#b34848",
            fg="#ffffff",
            activebackground="#d26060",
            activeforeground="#ffffff",
            relief="flat",
            padx=15,
            pady=8,
        )
        self.record_button.pack(side="left")
        self.file_button = tk.Button(
            controls,
            text="Identify a sound file",
            command=self.choose_sound_file,
            bg="#315f78",
            fg="#ffffff",
            activebackground="#46829f",
            activeforeground="#ffffff",
            relief="flat",
            padx=15,
            pady=8,
        )
        self.file_button.pack(side="left", padx=(8, 0))
        self.freeze_button = tk.Button(
            controls,
            text="Freeze graph",
            command=self.toggle_freeze,
            bg="#2b3943",
            fg="#ffffff",
            activebackground="#425563",
            activeforeground="#ffffff",
            relief="flat",
            padx=15,
            pady=8,
        )
        self.freeze_button.pack(side="left", padx=(8, 0))
        self.mode_button = tk.Button(
            controls,
            text=f"Mode: {self.analysis_mode}",
            command=self.toggle_analysis_mode,
            bg="#386b4b",
            fg="#ffffff",
            activebackground="#4b8d63",
            activeforeground="#ffffff",
            relief="flat",
            padx=15,
            pady=8,
        )
        self.mode_button.pack(side="left", padx=(8, 0))
        tk.Button(
            controls,
            text="Choose microphone",
            command=self.choose_microphone,
            bg="#244c60",
            fg="#ffffff",
            activebackground="#347087",
            activeforeground="#ffffff",
            relief="flat",
            padx=15,
            pady=8,
        ).pack(side="left", padx=(8, 0))
        tk.Button(
            controls,
            text="Restart microphone",
            command=self.start_capture,
            bg="#244c60",
            fg="#ffffff",
            activebackground="#347087",
            activeforeground="#ffffff",
            relief="flat",
            padx=15,
            pady=8,
        ).pack(side="left", padx=(8, 0))
        tk.Button(
            controls,
            text="Check for update",
            command=self.check_for_update,
            bg="#315f78",
            fg="#ffffff",
            activebackground="#46829f",
            activeforeground="#ffffff",
            relief="flat",
            padx=15,
            pady=8,
        ).pack(side="left", padx=(8, 0))
        tk.Button(
            controls,
            text="About",
            command=self.show_about,
            bg="#2b3943",
            fg="#ffffff",
            activebackground="#425563",
            activeforeground="#ffffff",
            relief="flat",
            padx=15,
            pady=8,
        ).pack(side="right", padx=(0, 8))
        tk.Button(
            controls,
            text="Quit",
            command=self.quit_app,
            bg="#2b3943",
            fg="#ffffff",
            activebackground="#425563",
            activeforeground="#ffffff",
            relief="flat",
            padx=15,
            pady=8,
        ).pack(side="right")
        if self.recording:
            self.record_button.configure(text="Finish take", bg="#b34048", activebackground="#d1555e")
        if self.file_analysis_active:
            self.file_button.configure(text="Reading sound file…", state="disabled")
        if self.frozen:
            self.freeze_button.configure(text="Resume live graph")

    def toggle_theme(self) -> None:
        self.theme_mode = "light" if self.theme_mode == "dark" else "dark"
        for child in self.winfo_children():
            child.destroy()
        self._build_ui()
        self._draw_grid()
        if self.latest_frame is not None:
            self.draw_spectrum(self.latest_frame)

    def toggle_analysis_mode(self) -> None:
        self.analysis_mode = "Instrument" if self.analysis_mode == "Voice" else "Voice"
        self.mode_button.configure(text=f"Mode: {self.analysis_mode}")
        if self.analysis_mode == "Instrument":
            self.recording_status.set("Instrument mode: recordings show note range, never a vocal classification.")
        else:
            self.recording_status.set("Voice mode: short samples show a neutral observed register.")

    def show_about(self) -> None:
        popup = tk.Toplevel(self)
        popup.title("About Harmonics Analysis")
        popup.configure(bg=self.popup_background())
        popup.resizable(False, False)
        popup.transient(self)
        body = tk.Frame(popup, bg=self.popup_background(), padx=22, pady=20)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="About Harmonics Analysis", fg=self.popup_ink(), bg=self.popup_background(), font=("Sans", 18, "bold")).pack(anchor="w")
        tk.Label(body, text=about_text(), fg=self.popup_ink(), bg=self.popup_background(), font=("Sans", 10), wraplength=500, justify="left").pack(anchor="w", pady=(10, 16))
        tk.Button(body, text="Close", command=popup.destroy, bg=self.popup_button(), fg=self.popup_ink(), activebackground=self.popup_active(), activeforeground=self.popup_ink(), relief="flat", padx=14, pady=7).pack(anchor="e")

    def choose_microphone(self) -> None:
        """Offer Windows capture devices, so a disconnected default is recoverable."""
        if os.name != "nt":
            self.recording_status.set("Microphone selection uses your system's default input on this platform.")
            return
        try:
            import sounddevice as sound
            devices = [(index, device) for index, device in enumerate(sound.query_devices()) if device["max_input_channels"] > 0]
        except Exception as error:
            self.recording_status.set(f"Could not list microphones: {error}")
            return
        if not devices:
            self.recording_status.set("No recording microphone was found. Connect one, then restart the microphone.")
            return
        popup = tk.Toplevel(self)
        popup.title("Choose microphone")
        popup.configure(bg=self.popup_background())
        popup.resizable(False, False)
        popup.transient(self)
        body = tk.Frame(popup, bg=self.popup_background(), padx=18, pady=16)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Choose microphone", fg=self.popup_ink(), bg=self.popup_background(), font=("Sans", 15, "bold")).pack(anchor="w")
        tk.Label(body, text="The app checks the selected input, uses its native rate if needed, then restarts capture.", fg=self.popup_muted(), bg=self.popup_background(), font=("Sans", 9), wraplength=460, justify="left").pack(anchor="w", pady=(3, 10))
        tk.Button(body, text="Use Windows default microphone", anchor="w", command=lambda: self.set_microphone(None, popup), bg=self.popup_button(), fg=self.popup_ink(), activebackground=self.popup_active(), activeforeground=self.popup_ink(), relief="flat", padx=10, pady=7).pack(fill="x", pady=2)
        for index, device in devices:
            host_api = sound.query_hostapis(device["hostapi"])["name"]
            label = f"{device['name']} — {host_api} ({int(device['default_samplerate'])} Hz)"
            tk.Button(body, text=label, anchor="w", command=lambda selected=index: self.set_microphone(selected, popup), bg=self.popup_button(), fg=self.popup_ink(), activebackground=self.popup_active(), activeforeground=self.popup_ink(), relief="flat", padx=10, pady=7, wraplength=450).pack(fill="x", pady=2)

    def set_microphone(self, device_index: int | None, popup: tk.Toplevel) -> None:
        self.input_device = device_index
        popup.destroy()
        self.start_capture()

    def check_for_update(self) -> None:
        """Check GitHub Releases on demand; microphone audio is never sent."""
        self.recording_status.set("Checking GitHub Releases for an update…")
        threading.Thread(target=self.check_for_update_worker, daemon=True, name="update-check").start()

    def check_for_update_worker(self) -> None:
        try:
            update = fetch_update_info(os.name == "nt")
            self.after(0, self.finish_update_check, update, None)
        except Exception as error:
            self.after(0, self.finish_update_check, None, str(error))

    def finish_update_check(self, update: UpdateInfo | None, error: str | None) -> None:
        if error:
            self.recording_status.set("Could not check for updates.")
            self.detail.set(f"Update check failed: {error}")
            return
        if update is None:
            self.recording_status.set("")
            self.detail.set(f"You are up to date (v{APP_VERSION}).")
            return
        self.recording_status.set(f"Version {update.version} is available.")
        if messagebox.askyesno("Harmonics Analysis update", f"Version {update.version} is available from GitHub Releases. Download and verify it now?", parent=self):
            threading.Thread(target=self.download_update_worker, args=(update,), daemon=True, name="update-download").start()

    def download_update_worker(self, update: UpdateInfo) -> None:
        try:
            if os.name == "nt":
                if not getattr(sys, "frozen", False):
                    raise RuntimeError("Windows self-updates are available from the packaged .exe, not from a source checkout.")
                current = os.path.abspath(sys.executable)
                destination = f"{current}.new"
                download_verified(update.asset_url, update.checksum_url, destination)
                script = f"{current}.update.cmd"
                quoted_current = subprocess.list2cmdline([current])
                quoted_new = subprocess.list2cmdline([destination])
                with open(script, "w", encoding="utf-8", newline="\r\n") as output:
                    output.write(
                        f"@echo off\r\nping 127.0.0.1 -n 3 > nul\r\n"
                        f"set \"APP={current}\"\r\nset \"NEW={destination}\"\r\nset \"BACKUP={current}.previous\"\r\n"
                        f"move /Y {quoted_current} \"%BACKUP%\"\r\n"
                        f"if errorlevel 1 goto :done\r\nmove /Y {quoted_new} {quoted_current}\r\n"
                        f"if errorlevel 1 (move /Y \"%BACKUP%\" {quoted_current} & goto :done)\r\n"
                        f"start \"\" {quoted_current}\r\ndel \"%BACKUP%\"\r\n:done\r\ndel \"%~f0\"\r\n"
                    )
                self.after(0, self.finish_windows_update, script, update.version)
                return
            destination = os.path.join(linux_update_staging_directory(), f"harmonics-analysis_{update.version}_all.deb")
            download_verified(update.asset_url, update.checksum_url, destination)
            self.after(0, self.finish_linux_update, destination, update.version)
        except Exception as error:
            self.after(0, self.update_download_failed, str(error))

    def finish_windows_update(self, script: str, version: str) -> None:
        self.recording_status.set(f"Version {version} verified. Restarting to apply it…")
        subprocess.Popen(["cmd", "/c", script], close_fds=True)
        self.after(400, self.quit_app)

    def finish_linux_update(self, destination: str, version: str) -> None:
        try:
            # Polkit displays the desktop's normal authentication dialog only
            # if needed; no terminal or copied sudo command is involved.
            process = subprocess.Popen(linux_update_command(destination), start_new_session=True)
            self.recording_status.set(f"Version {version} verified. Approve the system dialog to install safely.")
            threading.Thread(target=self.wait_for_linux_update, args=(process, version), daemon=True, name="linux-update-install").start()
        except Exception as error:
            self.recording_status.set("Automatic update could not start; the installed app was not changed.")
            self.detail.set(f"The verified package remains at {destination}. {error}")

    def wait_for_linux_update(self, process: subprocess.Popen[object], version: str) -> None:
        installed = process.wait() == 0
        self.after(0, self.finish_linux_install, version, installed)

    def finish_linux_install(self, version: str, installed: bool) -> None:
        if installed:
            self.recording_status.set(f"Version {version} installed safely. Restart Harmonics Analysis to use it.")
            if messagebox.askyesno("Update installed", f"Version {version} is installed. Restart now?", parent=self):
                launcher = shutil.which("harmonics-analysis")
                if launcher:
                    subprocess.Popen([launcher], start_new_session=True)
                    self.quit_app()
        else:
            self.recording_status.set("Update was cancelled or failed; the existing app remains installed.")

    def update_download_failed(self, error: str) -> None:
        self.recording_status.set("Update was not installed.")
        self.detail.set(f"The update download was rejected or failed: {error}")


    def _draw_grid(self) -> None:
        self.canvas.delete("grid")
        base_zone = self.hz_x(400)
        self.canvas.create_rectangle(0, 0, base_zone, SPECTRUM_HEIGHT, fill=self.scope_base_zone, outline="", tags="grid")
        self.canvas.create_text(
            10,
            14,
            text="BASE NOTE\n(roughly 70–400 Hz)",
            anchor="nw",
            fill=self.scope_text,
            font=("Sans", 8, "bold"),
            tags="grid",
        )
        self.canvas.create_text(
            base_zone + 10,
            14,
            text="OVERTONES / HARMONICS",
            anchor="nw",
            fill=self.scope_text,
            font=("Sans", 8, "bold"),
            tags="grid",
        )
        for db in range(int(MIN_DB), int(MAX_DB) + 1, 20):
            y = self.db_y(db)
            self.canvas.create_line(0, y, CANVAS_WIDTH, y, fill=self.scope_grid, tags="grid")
            self.canvas.create_text(7, y - 2, text=f"{db} dB", anchor="w", fill=self.scope_text, font=("Sans", 8), tags="grid")
        for hz in range(0, MAX_FREQUENCY + 1, 1000):
            x = self.hz_x(hz)
            self.canvas.create_line(x, 0, x, SPECTRUM_HEIGHT, fill=self.scope_grid, tags="grid")
            label = "0 Hz" if hz == 0 else f"{hz // 1000} kHz"
            self.canvas.create_text(x + 3, SPECTRUM_HEIGHT - 8, text=label, anchor="w", fill=self.scope_text, font=("Sans", 8), tags="grid")

    def hz_x(self, hz: float) -> float:
        return hz / MAX_FREQUENCY * CANVAS_WIDTH

    def db_y(self, db: float) -> float:
        clipped = max(MIN_DB, min(MAX_DB, db))
        return (MAX_DB - clipped) / (MAX_DB - MIN_DB) * SPECTRUM_HEIGHT

    def start_capture(self) -> None:
        self.stop_capture()
        self.stop_event = threading.Event()
        self.capture_generation += 1
        self.running = True
        self.last_frame_at = time.monotonic()
        self.note_display.set("—")
        self.base_note.set("Listening for a steady “aaa”")
        self.tuning.set("")
        self.recipe.set("Waiting for a harmonic stack")
        self.frozen = False
        self.freeze_button.configure(text="Freeze graph")
        self.status.set("Listening — speak comfortably, not loudly.")
        self.detail.set("A steady sound makes the harmonic pattern easiest to see.")
        self.worker = threading.Thread(target=self.capture_loop, args=(self.stop_event, self.capture_generation), daemon=True, name="harmonic-capture")
        self.worker.start()

    def stop_capture(self) -> None:
        self.stop_event.set()
        stream = self.input_stream
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass
        self.input_stream = None
        process = self.process
        if process and process.poll() is None:
            process.terminate()
        self.process = None

    def start_baseline_calibration(self) -> None:
        if self.recording:
            self.recording_status.set("Finish the current take before setting a quiet baseline.")
            return
        self.noise_floor = None
        self.baseline_samples.clear()
        self.calibrating = True
        self.note_display.set("…")
        self.base_note.set("Sampling the quiet room")
        self.tuning.set("")
        self.recipe.set("Please stay quiet for about two seconds.")
        self.status.set("Calibrating the noise filter — do not speak yet.")
        self.noise_filter.set("Noise filter: measuring room noise…")
        self.detail.set("Sound at or below this quiet-room level will be hidden from the graph.")

    def clear_noise_baseline(self) -> None:
        self.noise_floor = None
        self.calibrating = False
        self.baseline_samples.clear()
        self.noise_filter.set("Noise filter: off — all microphone sound is shown")
        self.detail.set("Noise filter cleared. Set a quiet baseline again if room noise is distracting.")

    def complete_baseline(self) -> None:
        sample_count = len(self.baseline_samples)
        self.noise_floor = tuple(
            min(MAX_DB, sorted(sample[index] for sample in self.baseline_samples)[sample_count // 2] + 6.0)
            for index in range(len(self.baseline_samples[0]))
        )
        voice_low = max(1, math.ceil(70 * FRAME_SIZE / SAMPLE_RATE))
        voice_high = min(len(self.noise_floor) - 1, math.floor(1000 * FRAME_SIZE / SAMPLE_RATE))
        average = sum(self.noise_floor[voice_low:voice_high + 1]) / (voice_high - voice_low + 1)
        self.noise_filter.set(
            f"Noise filter: on — hides room sound below ≈ {average:.0f} dBFS"
        )

        self.calibrating = False
        self.baseline_samples.clear()
        self.detail.set("Quiet baseline set. Sing normally; the graph now hides background sound at or below it.")
    def toggle_freeze(self) -> None:
        self.frozen = not self.frozen
        if self.frozen:
            self.freeze_button.configure(text="Resume live graph")
            if not self.recording:
                self.recording_status.set("Graph frozen — microphone capture continues.")
        else:
            self.freeze_button.configure(text="Freeze graph")
            if not self.recording:
                self.recording_status.set("")

    def gate_noise(self, levels: tuple[float, ...]) -> tuple[float, ...]:
        return gate_levels(levels, self.noise_floor)

    def toggle_recording(self) -> None:
        if self.recording:
            self.finish_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        if self.calibrating:
            self.recording_status.set("Finish the quiet baseline before recording a take.")
            return
        if self.file_analysis_active:
            self.recording_status.set("Wait for the selected sound file to finish analysing.")
            return
        level_count = int(MAX_FREQUENCY * FRAME_SIZE / SAMPLE_RATE) + 1
        self.active_take = TakeAnalysis.start(level_count)
        self.recording = True
        self.record_button.configure(text="Finish take", bg="#b34048", activebackground="#d1555e")
        activity = "play a phrase" if self.analysis_mode == "Instrument" else "sing a phrase"
        self.recording_status.set(f"Recording now — {activity}, then press Finish take.")

    def finish_recording(self) -> None:
        take = self.active_take
        self.recording = False
        self.active_take = None
        self.record_button.configure(text="Record a take", bg="#7b3038", activebackground="#a3434d")
        self.recording_status.set("")
        if take is not None:
            self.show_take_summary("Your recorded take", take)

    def show_take_summary(self, title: str, take: TakeAnalysis) -> None:
        background = self.popup_background()
        ink = self.popup_ink()
        muted = self.popup_muted()
        summary = tk.Toplevel(self)
        summary.title(title)
        summary.configure(bg=background)
        summary.resizable(False, False)
        summary.transient(self)

        body = tk.Frame(summary, bg=background, padx=20, pady=18)
        body.pack(fill="both", expand=True)
        duration = take.frame_count * FRAME_SIZE / SAMPLE_RATE
        tk.Label(body, text=title, fg=ink, bg=background, font=("Sans", 19, "bold")).pack(anchor="w")
        tk.Label(
            body,
            text=f"{duration:.1f} seconds analysed locally",
            fg=muted,
            bg=background,
            font=("Sans", 10),
        ).pack(anchor="w", pady=(3, 14))

        details = tk.Frame(body, bg=background)
        details.pack(fill="x", pady=(0, 15))
        range_name, range_description = (instrument_range_label(take) if self.analysis_mode == "Instrument" else vocal_range_label(take))
        range_card = tk.Frame(details, bg=self.popup_card(), padx=14, pady=12, highlightthickness=1, highlightbackground=self.popup_border())
        range_card.pack(side="right", fill="y", padx=(14, 0))
        tk.Label(range_card, text="Likely vocal range", fg=muted, bg=self.popup_card(), font=("Sans", 9, "bold")).pack(anchor="w")
        tk.Label(range_card, text=range_name, fg=self.scope_harmonics, bg=self.popup_card(), font=("Sans", 18, "bold")).pack(anchor="w", pady=(2, 2))
        tk.Label(range_card, text=range_description, fg=ink, bg=self.popup_card(), font=("Sans", 8), wraplength=190, justify="left").pack(anchor="w")
        notes_area = tk.Frame(details, bg=background)
        notes_area.pack(side="left", fill="both", expand=True)
        tk.Label(notes_area, text="Notes you sang", fg=muted, bg=background, font=("Sans", 10, "bold")).pack(anchor="w")
        note_runs = [run for run in take.note_runs if run.frames >= 2]
        if note_runs:
            note_text = "  →  ".join(
                f"{run.name}  {run.frames * FRAME_SIZE / SAMPLE_RATE:.1f}s"
                for run in note_runs
            )
        else:
            note_text = "No steady notes were detected. Try a clearer, held vowel."
        tk.Label(
            notes_area,
            text=note_text,
            fg=self.scope_harmonics,
            bg=background,
            font=("Sans", 11, "bold"),
            wraplength=390,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        tk.Label(body, text="Whole-take harmonics", fg=muted, bg=background, font=("Sans", 10, "bold")).pack(anchor="w")
        tk.Label(
            body,
            text="Average energy at H1–H6 across frames with a clear base note",
            fg=muted,
            bg=background,
            font=("Sans", 9),
        ).pack(anchor="w", pady=(2, 5))
        harmonic_canvas = tk.Canvas(body, width=610, height=150, bg=self.popup_card(), highlightthickness=1, highlightbackground=self.popup_border())
        harmonic_canvas.pack(anchor="w")
        harmonics = take.harmonic_averages()
        visible = [value for value in harmonics if value is not None]
        if visible:
            floor = min(-70.0, min(visible))
            ceiling = max(visible)
            for index, value in enumerate(harmonics):
                left = 34 + index * 96
                height = 0.0 if value is None else 100 * (value - floor) / max(ceiling - floor, 1.0)
                harmonic_canvas.create_rectangle(left, 122 - height, left + 54, 122, fill="#f6b73c", outline="")
                harmonic_canvas.create_text(left + 27, 136, text=f"H{index + 1}", fill=ink, font=("Sans", 9, "bold"))
                harmonic_canvas.create_text(left + 27, 114 - height, text="—" if value is None else f"{value:.0f}", fill=self.scope_harmonics, font=("Sans", 8))
        else:
            harmonic_canvas.create_text(305, 75, text="Hold a steady note to map its harmonics.", fill=muted, font=("Sans", 11))

        average_levels = take.average_levels()
        tk.Label(
            body,
            text=f"Overall voice colour: {voice_colour(average_levels)}  ·  Pitch steadiness: {take.pitch_stability()}",
            fg=ink,
            bg=background,
            font=("Sans", 10),
        ).pack(anchor="w", pady=(12, 0))
        tk.Button(
            body,
            text="Export summary",
            command=lambda: self.export_take_summary(title, take),
            bg=self.popup_button(),
            fg=ink,
            activebackground=self.popup_active(),
            activeforeground=ink,
            relief="flat",
            padx=14,
            pady=7,
        ).pack(anchor="w", pady=(12, 0))
        tk.Button(
            body,
            text="Close",
            command=summary.destroy,
            bg=self.popup_button(),
            fg=ink,
            activebackground=self.popup_active(),
            activeforeground=ink,
            relief="flat",
            padx=14,
            pady=7,
        ).pack(anchor="e", pady=(12, 0))

    def export_take_summary(self, title: str, take: TakeAnalysis) -> None:
        """Save a small local text report; recording data itself is not kept."""
        path = filedialog.asksaveasfilename(parent=self, title="Export take summary", defaultextension=".txt", initialfile="harmonics-take-summary.txt", filetypes=(("Text file", "*.txt"),))
        if not path:
            return
        name, description = (instrument_range_label(take) if self.analysis_mode == "Instrument" else vocal_range_label(take))
        note_runs = [f"{run.name} ({run.frames * FRAME_SIZE / SAMPLE_RATE:.1f}s)" for run in take.note_runs if run.frames >= 2]
        report = "\n".join((title, f"Version: {APP_VERSION}", f"Vocal range: {name}", description, f"Pitch steadiness: {take.pitch_stability()}", f"Notes: {', '.join(note_runs) or 'No sustained notes'}", "", "Audio is analysed locally and is not included in this report."))
        try:
            with open(path, "w", encoding="utf-8") as output:
                output.write(report + "\n")
            self.recording_status.set(f"Take summary exported to {os.path.basename(path)}")
        except OSError as error:
            self.recording_status.set(f"Could not export summary: {error}")

    def popup_background(self) -> str:
        return "#fff8e9" if self.theme_mode == "light" else "#111820"

    def popup_card(self) -> str:
        return "#f3ead6" if self.theme_mode == "light" else "#081820"

    def popup_ink(self) -> str:
        return "#192632" if self.theme_mode == "light" else "#f2f7fb"

    def popup_muted(self) -> str:
        return "#586b75" if self.theme_mode == "light" else "#91a8b8"

    def popup_border(self) -> str:
        return "#d1ae72" if self.theme_mode == "light" else "#245164"

    def popup_button(self) -> str:
        return "#e5dbc3" if self.theme_mode == "light" else "#26313a"

    def popup_active(self) -> str:
        return "#d1ae72" if self.theme_mode == "light" else "#3a4954"

    def choose_sound_file(self) -> None:
        if self.recording:
            self.recording_status.set("Finish the current take before choosing a sound file.")
            return
        if self.file_analysis_active:
            return
        file_path = filedialog.askopenfilename(
            parent=self,
            title="Identify a sound file",
            filetypes=(
                ("Sound files", "*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.opus *.aiff *.wma"),
                ("All files", "*"),
            ),
        )
        if not file_path:
            return
        self.file_analysis_active = True
        self.file_button.configure(text="Reading sound file…", state="disabled")
        self.recording_status.set(f"Analysing {os.path.basename(file_path)}…")
        threading.Thread(
            target=self.analyze_sound_file,
            args=(file_path, self.noise_floor),
            daemon=True,
            name="sound-file-analysis",
        ).start()

    def analyze_sound_file(self, file_path: str, noise_floor: tuple[float, ...] | None) -> None:
        level_count = int(MAX_FREQUENCY * FRAME_SIZE / SAMPLE_RATE) + 1
        take = TakeAnalysis.start(level_count)
        error: str | None = None
        try:
            command = [
                bundled_tool("ffmpeg.exe" if os.name == "nt" else "ffmpeg"),
                "-nostdin",
                "-v",
                "error",
                "-i",
                file_path,
                "-vn",
                "-ac",
                str(CHANNELS),
                "-ar",
                str(SAMPLE_RATE),
                "-f",
                "s16le",
                "-",
            ]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert process.stdout is not None
            pending = bytearray()
            wanted = FRAME_SIZE * 2
            while True:
                part = process.stdout.read(wanted - len(pending))
                if not part:
                    break
                pending.extend(part)
                if len(pending) < wanted:
                    continue
                raw = bytes(pending[:wanted])
                del pending[:wanted]
                take.add_frame(analyze(raw), noise_floor)
            diagnostics = process.stderr.read().decode(errors="replace") if process.stderr else ""
            if process.wait() != 0:
                error = diagnostics.strip() or "The selected file could not be decoded as sound."
            elif not take.frame_count:
                error = "No audio samples were found in the selected file."
        except Exception as exception:
            error = str(exception)
        self.after(0, self.finish_sound_file_analysis, file_path, take, error)

    def finish_sound_file_analysis(self, file_path: str, take: TakeAnalysis, error: str | None) -> None:
        self.file_analysis_active = False
        self.file_button.configure(text="Identify a sound file", state="normal")
        if error:
            self.recording_status.set("Sound-file analysis could not finish.")
            self.detail.set(error)
            return
        self.recording_status.set("")
        self.detail.set(f"Analysed {os.path.basename(file_path)} locally. The summary shows its notes and average harmonics.")
        self.show_take_summary(f"File: {os.path.basename(file_path)}", take)
    def capture_windows_loop(self, stop_event: threading.Event, generation: int) -> None:
        try:
            import sounddevice as sound
            failures: list[str] = []
            for rate in windows_capture_candidates(sound, self.input_device):
                if stop_event.is_set() or generation != self.capture_generation:
                    return
                pending = bytearray()

                def callback(indata: buffer, _frames: int, _time: object, status: object) -> None:
                    if stop_event.is_set() or generation != self.capture_generation:
                        return
                    if status:
                        self.windows_overflow_count += 1
                    pending.extend(resample_pcm_16le(bytes(indata), rate))
                    wanted = FRAME_SIZE * 2
                    while len(pending) >= wanted:
                        raw = bytes(pending[:wanted])
                        del pending[:wanted]
                        self.put_frame(analyze(raw))

                try:
                    # `latency=high` uses the Windows shared-mode buffer and is
                    # much more reliable across USB, Bluetooth, WASAPI, and MME
                    # inputs than assuming a low-latency 48 kHz endpoint.
                    sound.check_input_settings(device=self.input_device, channels=CHANNELS, samplerate=rate, dtype="int16")
                    stream = sound.RawInputStream(samplerate=rate, channels=CHANNELS, dtype="int16", blocksize=0, device=self.input_device, latency="high", callback=callback)
                    self.capture_rate = rate
                    self.input_stream = stream
                    with stream:
                        while not stop_event.wait(0.1):
                            if not stream.active:
                                raise RuntimeError("Windows microphone stream stopped unexpectedly.")
                    return
                except Exception as error:
                    failures.append(f"{rate:.0f} Hz: {error}")
                    if self.input_stream is not None:
                        try:
                            self.input_stream.close()
                        except Exception:
                            pass
                        self.input_stream = None
            selected = "the selected microphone" if self.input_device is not None else "the Windows default microphone"
            self.put_frame(RuntimeError(f"Could not open {selected}. Try another entry in Choose microphone. Attempts: {'; '.join(failures)}"))
        except Exception as error:
            if not stop_event.is_set() and generation == self.capture_generation:
                selected = "the selected microphone" if self.input_device is not None else "the default microphone"
                self.put_frame(RuntimeError(f"Could not open {selected}. Use Choose microphone, then restart it. Details: {error}"))
        finally:
            if generation == self.capture_generation:
                self.input_stream = None


    def capture_loop(self, stop_event: threading.Event, generation: int) -> None:
        if os.name == "nt":
            self.capture_windows_loop(stop_event, generation)
            return
        command = [
            "arecord",
            "-q",
            "-D",
            "default",
            "-f",
            "S16_LE",
            "-r",
            str(SAMPLE_RATE),
            "-c",
            str(CHANNELS),
            "-t",
            "raw",
            "-",
        ]
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if generation == self.capture_generation:
                self.process = process
            assert process.stdout is not None
            pending = bytearray()
            wanted = FRAME_SIZE * 2
            while not stop_event.is_set():
                part = process.stdout.read(wanted - len(pending))
                if not part:
                    raise RuntimeError("Microphone stream ended. Check your input device and restart it.")
                pending.extend(part)
                if len(pending) < wanted:
                    continue
                raw = bytes(pending[:wanted])
                del pending[:wanted]
                self.put_frame(analyze(raw))
        except Exception as error:
            if not stop_event.is_set() and generation == self.capture_generation:
                self.put_frame(error)
        finally:
            if process and process.poll() is None:
                process.terminate()
            if self.process is process:
                self.process = None

    def put_frame(self, frame: SpectrumFrame | Exception) -> None:
        if isinstance(frame, SpectrumFrame):
            self.last_frame_at = time.monotonic()
        try:
            self.frames.put_nowait(frame)
        except queue.Full:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            self.frames.put_nowait(frame)

    def monitor_capture_health(self) -> None:
        """Recover from a Windows endpoint that stays open but stops sending audio."""
        if self.running and not self.stop_event.is_set() and time.monotonic() - self.last_frame_at > 4.0:
            if self.mic_retries < 2:
                self.mic_retries += 1
                self.recording_status.set(f"Microphone stalled — reconnecting ({self.mic_retries}/2)…")
                self.start_capture()
            else:
                self.running = False
                self.recording_status.set("Microphone stopped sending audio. Choose a microphone or restart capture.")
        elif os.name == "nt" and self.windows_overflow_count:
            count = self.windows_overflow_count
            self.windows_overflow_count = 0
            self.detail.set(f"Windows capture recovered from {count} buffer warning(s). If this repeats, choose another microphone.")
        if self.winfo_exists():
            self.after(1_000, self.monitor_capture_health)

    def consume_frames(self) -> None:
        latest: SpectrumFrame | Exception | None = None
        while True:
            try:
                latest = self.frames.get_nowait()
            except queue.Empty:
                break
        if isinstance(latest, Exception):
            self.running = False
            self.note_display.set("—")
            self.base_note.set("Microphone unavailable")
            self.tuning.set("")
            self.recipe.set("Harmonics unavailable")
            self.status.set("Microphone error")
            self.detail.set(str(latest))
            if os.name == "nt" and self.mic_retries < 2:
                self.mic_retries += 1
                self.recording_status.set(f"Microphone retry {self.mic_retries}/2 in 2 seconds…")
                self.after(2_000, self.start_capture)
        elif isinstance(latest, SpectrumFrame):
            self.mic_retries = 0
            self.latest_frame = latest
            if self.calibrating:
                self.baseline_samples.append(latest.levels)
                remaining = self.baseline_frame_count - len(self.baseline_samples)
                if remaining > 0:
                    self.note_display.set("…")
                    self.base_note.set("Sampling the quiet room")
                    self.tuning.set("")
                    self.recipe.set(f"Please stay quiet — {remaining} samples left")
                    self.status.set("Calibrating the noise filter…")
                    self.detail.set("This takes about two seconds. Your normal view returns automatically.")
                else:
                    self.complete_baseline()
                    if not self.frozen:
                        self.draw_spectrum(latest)
            else:
                if self.recording and self.active_take is not None:
                    self.active_take.add_frame(latest, self.noise_floor)
                if not self.frozen:
                    self.draw_spectrum(latest)
        if self.winfo_exists():
            self.after(35, self.consume_frames)

    def draw_spectrum(self, frame: SpectrumFrame) -> None:
        self.canvas.delete("spectrum")
        self.canvas.delete("harmonics")
        levels = self.gate_noise(frame.levels)
        raw_fundamental, confidence = estimate_pitch(levels)
        fundamental = self.pitch_lock.update(raw_fundamental, confidence)
        self.draw_voice_profile(levels)
        self.draw_input_level(frame.rms_db)


        points: list[float] = []
        for index, db in enumerate(levels):
            hz = index * SAMPLE_RATE / FRAME_SIZE
            points.extend((self.hz_x(hz), self.db_y(db)))
        if len(points) >= 4:
            self.canvas.create_line(*points, fill=self.scope_trace, width=2, smooth=True, tags="spectrum")

        if frame.rms_db < VOICE_RMS_DB:
            self.note_display.set("—")
            self.base_note.set("I cannot hear a steady note yet")
            self.tuning.set("")
            self.recipe.set("Hold a comfortable “aaa” for a moment")
            self.note_trail.set("Note trail: listening")
            self.range_coach.set("Range coach: start with a comfortable, steady vowel")
            self.status.set("Speak closer or check the microphone.")
            self.detail.set("Once the cyan line rises, the app will name your base note and its overtones.")
            return

        if fundamental and confidence >= 0.42:
            note = note_for_frequency(fundamental)
            self.note_display.set(note)
            self.base_note.set(f"Base note ≈ {fundamental:.0f} Hz")
            self.tuning.set(tuning_for_frequency(fundamental))
            if not self.recent_note_names or self.recent_note_names[-1] != note:
                self.recent_note_names.append(note)
                del self.recent_note_names[:-8]
            self.note_trail.set(f"Note trail: {'  →  '.join(self.recent_note_names)}")
            if fundamental < 130:
                self.range_coach.set("Range coach: low note held — now try a comfortable middle vowel")
            elif fundamental > 350:
                self.range_coach.set("Range coach: high note held — add a low vowel for a fuller range")
            else:
                self.range_coach.set("Range coach: middle note held — add a sustained low and high vowel")
            self.recipe.set(harmonic_note_stack(fundamental))
            quality = "clear" if confidence >= 0.70 else "tentative"
            self.status.set(f"{quality.capitalize()} pitch — cyan peaks near amber guides are its overtones.")
            for number in range(1, 9):
                harmonic = fundamental * number
                if harmonic > MAX_FREQUENCY:
                    break
                x = self.hz_x(harmonic)
                self.canvas.create_line(x, 48, x, SPECTRUM_HEIGHT - 20, fill=self.scope_harmonics, dash=(3, 5), tags="harmonics")
            noise_detail = " Noise baseline is on." if self.noise_floor is not None else ""
            self.detail.set(
                f"Input level {frame.rms_db:.1f} dBFS · pitch confidence {confidence:.0%}. Speech can show a note, but only held vowels affect range.{noise_detail}"
            )
        else:
            self.note_display.set("—")
            self.base_note.set("No steady base note yet")
            self.tuning.set("")
            self.recipe.set("Hold one calm “aaa” for about one second")
            self.note_trail.set("Note trail: waiting for a clear pitch")
            self.status.set("Try holding one calm “aaa” for about one second.")
            noise_detail = " Noise baseline is on." if self.noise_floor is not None else ""
            self.detail.set(f"Input level {frame.rms_db:.1f} dBFS · a steady vowel makes the amber harmonic guides appear.{noise_detail}")

    def draw_input_level(self, rms_db: float) -> None:
        """Show a compact level meter so users can distinguish silence from bad pitch."""
        self.level_canvas.delete("all")
        width = CANVAS_WIDTH
        ratio = max(0.0, min(1.0, (rms_db - MIN_DB) / (MAX_DB - MIN_DB)))
        self.level_canvas.create_rectangle(0, 2, width, 16, fill=self.scope_grid, outline="")
        color = "#cc4b4b" if rms_db > -6 else self.scope_harmonics if rms_db > -18 else self.scope_trace
        self.level_canvas.create_rectangle(0, 2, width * ratio, 16, fill=color, outline="")
        for marker in (-60, -30, -12, 0):
            x = width * (marker - MIN_DB) / (MAX_DB - MIN_DB)
            self.level_canvas.create_line(x, 1, x, 17, fill=self.scope_background)
        guidance = "too quiet" if rms_db < VOICE_RMS_DB else "CLIPPING — lower microphone gain" if rms_db > -6 else "strong" if rms_db > -12 else "comfortable"
        self.input_level.set(f"Input level: {rms_db:.1f} dBFS — {guidance}")

    def draw_voice_profile(self, levels: tuple[float, ...]) -> None:
        """Draw a compact, friendly spectral balance view beside the note."""
        self.voice_canvas.delete("all")
        columns = 18
        rows = 5
        tile_width = 11
        tile_height = 11
        gap = 2
        palette = (
            ("#123b3e", "#2dd4bf"),
            ("#4a3b1b", "#f6c85f"),
            ("#42202f", "#f472b6"),
        )
        quality = [0.0, 0.0, 0.0]
        quality_counts = [0, 0, 0]

        for column in range(columns):
            first = column * len(levels) // columns
            last = max(first + 1, (column + 1) * len(levels) // columns)
            peak = max(levels[first:last])
            frequency = (first + last) * SAMPLE_RATE / (2 * FRAME_SIZE)
            group = 0 if frequency < 750 else 1 if frequency < 2_800 else 2
            quality[group] += peak
            quality_counts[group] += 1
            filled = max(0, min(rows, math.ceil((peak - MIN_DB) / (MAX_DB - MIN_DB) * rows)))
            muted, active = palette[group]
            x = 9 + column * (tile_width + gap)
            for row in range(rows):
                y = 8 + row * (tile_height + gap)
                color = active if row >= rows - filled else muted
                self.voice_canvas.create_rectangle(x, y, x + tile_width, y + tile_height, fill=color, outline="")

        averages = [total / count if count else MIN_DB for total, count in zip(quality, quality_counts)]
        strongest = max(range(3), key=averages.__getitem__)
        labels = (
            "Warm / lower-frequency weighted",
            "Balanced / midrange weighted",
            "Bright / upper-frequency weighted",
        )
        self.voice_quality.set(labels[strongest])
    def quit_app(self) -> None:
        self.stop_capture()
        self.destroy()


if __name__ == "__main__":
    HarmonicViewer().mainloop()
