#!/usr/bin/env python3
"""Local, live voice-harmonics viewer.

Speak a steady vowel and watch the cyan spectrum peaks line up with amber
harmonic guides.  The viewer explains the base note and overtone pattern in
plain language; no voice-analysis terminology is required.
"""

from __future__ import annotations

import cmath
import math
import os
import queue
import struct
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import filedialog


SAMPLE_RATE = 48_000
CHANNELS = 1
FRAME_SIZE = 4_096
MAX_FREQUENCY = 5_000
MIN_DB = -80.0
MAX_DB = 0.0
CANVAS_WIDTH = 670
SPECTRUM_HEIGHT = 350


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

@dataclass(frozen=True)
class SpectrumFrame:
    levels: tuple[float, ...]
    rms_db: float
    fundamental_hz: float | None


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
        if frame.rms_db < -55.0:
            self.last_note = None
            return
        levels = gate_levels(frame.levels, noise_floor)
        fundamental = detect_fundamental(levels)
        if fundamental is None:
            self.last_note = None
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
    samples = struct.unpack(f"<{FRAME_SIZE}h", raw)
    rms = math.sqrt(sum(sample * sample for sample in samples) / FRAME_SIZE)
    rms_db = 20.0 * math.log10(max(rms / 32768.0, 1e-8))

    windowed = [
        complex(sample * (0.5 - 0.5 * math.cos(2.0 * math.pi * index / (FRAME_SIZE - 1))), 0.0)
        for index, sample in enumerate(samples)
    ]
    transform = fft(windowed)
    max_bin = min(FRAME_SIZE // 2, int(MAX_FREQUENCY * FRAME_SIZE / SAMPLE_RATE))
    levels = []
    for index in range(max_bin + 1):
        magnitude = abs(transform[index]) * 2.0 / FRAME_SIZE
        levels.append(20.0 * math.log10(max(magnitude / 32768.0, 1e-8)))

    return SpectrumFrame(tuple(levels), rms_db, detect_fundamental(levels))

def detect_fundamental(levels: tuple[float, ...] | list[float]) -> float | None:
    """Estimate the strongest voice-range peak after optional noise gating."""
    low_bin = max(1, math.ceil(70 * FRAME_SIZE / SAMPLE_RATE))
    high_bin = min(len(levels) - 1, math.floor(400 * FRAME_SIZE / SAMPLE_RATE))
    strongest = max(range(low_bin, high_bin + 1), key=levels.__getitem__)
    if levels[strongest] <= -58.0:
        return None
    left, center, right = levels[strongest - 1], levels[strongest], levels[strongest + 1]
    curve = left - 2.0 * center + right
    offset = 0.0 if curve == 0.0 else max(-0.5, min(0.5, 0.5 * (left - right) / curve))
    return (strongest + offset) * SAMPLE_RATE / FRAME_SIZE


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
    """Give a cautious, range-based voice label from the notes observed in a take."""
    if take.lowest_frequency is None or take.highest_frequency is None:
        return "Not enough voiced sound", "Hold a few clear notes to estimate a vocal range."
    low = 69 + 12 * math.log2(take.lowest_frequency / 440.0)
    high = 69 + 12 * math.log2(take.highest_frequency / 440.0)
    profiles = (
        ("Bass", 40, 64),
        ("Baritone", 43, 67),
        ("Tenor", 48, 72),
        ("Alto", 53, 77),
        ("Soprano", 60, 84),
    )
    # Choose the common range with the largest overlap; centres break a tie.
    def score(profile: tuple[str, int, int]) -> tuple[float, float]:
        _name, bottom, top = profile
        overlap = max(0.0, min(high, top) - max(low, bottom))
        return overlap, -abs((low + high) / 2 - (bottom + top) / 2)
    name, _bottom, _top = max(profiles, key=score)
    return name, f"Observed range: {note_for_frequency(take.lowest_frequency)} to {note_for_frequency(take.highest_frequency)}. This is a range estimate, not a voice diagnosis."


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

        self._build_ui()
        self._draw_grid()
        self.start_capture()
        self.after(35, self.consume_frames)

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
        tk.Frame(note_panel, height=2, bg=line).pack(fill="x", pady=15)
        tk.Label(note_panel, text="Harmonic family", fg=muted_ink, bg=paper, font=("Sans", 10, "bold")).pack(anchor="w")
        tk.Label(note_panel, textvariable=self.recipe, fg=ink, bg=paper, font=("Sans", 10), wraplength=270, justify="left").pack(anchor="w", pady=(4, 0))
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
        tk.Label(body, text="The app restarts capture with the selected input.", fg=self.popup_muted(), bg=self.popup_background(), font=("Sans", 9)).pack(anchor="w", pady=(3, 10))
        for index, device in devices:
            label = f"{device['name']} ({int(device['default_samplerate'])} Hz)"
            tk.Button(body, text=label, anchor="w", command=lambda selected=index: self.set_microphone(selected, popup), bg=self.popup_button(), fg=self.popup_ink(), activebackground=self.popup_active(), activeforeground=self.popup_ink(), relief="flat", padx=10, pady=7, wraplength=450).pack(fill="x", pady=2)

    def set_microphone(self, device_index: int, popup: tk.Toplevel) -> None:
        self.input_device = device_index
        popup.destroy()
        self.start_capture()


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
        self.stop_event.clear()
        self.running = True
        self.note_display.set("—")
        self.base_note.set("Listening for a steady “aaa”")
        self.tuning.set("")
        self.recipe.set("Waiting for a harmonic stack")
        self.frozen = False
        self.freeze_button.configure(text="Freeze graph")
        self.status.set("Listening — speak comfortably, not loudly.")
        self.detail.set("A steady sound makes the harmonic pattern easiest to see.")
        self.worker = threading.Thread(target=self.capture_loop, daemon=True, name="harmonic-capture")
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
        self.recording_status.set("Recording now — sing a phrase, then press Finish take.")

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
        range_name, range_description = vocal_range_label(take)
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
    def capture_windows_loop(self) -> None:
        try:
            import sounddevice as sound

            def callback(indata: buffer, _frames: int, _time: object, status: object) -> None:
                if not self.stop_event.is_set():
                    self.put_frame(analyze(bytes(indata)))

            # Keep a stream reference so Restart/Quit can close PortAudio cleanly.
            # Explicit settings make a bad device/default give a useful error instead
            # of silently opening an incompatible input.
            sound.check_input_settings(
                device=self.input_device,
                channels=CHANNELS,
                samplerate=SAMPLE_RATE,
                dtype="int16",
            )
            stream = sound.RawInputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=FRAME_SIZE,
                device=self.input_device,
                latency="low",
                never_drop_input=True,
                callback=callback,
            )
            self.input_stream = stream
            with stream:
                while not self.stop_event.wait(0.1):
                    pass
        except Exception as error:
            if not self.stop_event.is_set():
                selected = "the selected microphone" if self.input_device is not None else "the default microphone"
                self.put_frame(RuntimeError(f"Could not open {selected}. Use Choose microphone, then restart it. Details: {error}"))
        finally:
            self.input_stream = None


    def capture_loop(self) -> None:
        if os.name == "nt":
            self.capture_windows_loop()
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
        try:
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            assert self.process.stdout is not None
            pending = bytearray()
            wanted = FRAME_SIZE * 2
            while not self.stop_event.is_set():
                part = self.process.stdout.read(wanted - len(pending))
                if not part:
                    raise RuntimeError("Microphone stream ended. Check your input device and restart it.")
                pending.extend(part)
                if len(pending) < wanted:
                    continue
                raw = bytes(pending[:wanted])
                del pending[:wanted]
                self.put_frame(analyze(raw))
        except Exception as error:
            if not self.stop_event.is_set():
                self.put_frame(error)
        finally:
            process = self.process
            if process and process.poll() is None:
                process.terminate()
            self.process = None

    def put_frame(self, frame: SpectrumFrame | Exception) -> None:
        try:
            self.frames.put_nowait(frame)
        except queue.Full:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            self.frames.put_nowait(frame)

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
        elif isinstance(latest, SpectrumFrame):
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
        fundamental = detect_fundamental(levels)
        self.draw_voice_profile(levels)
        self.draw_input_level(frame.rms_db)


        points: list[float] = []
        for index, db in enumerate(levels):
            hz = index * SAMPLE_RATE / FRAME_SIZE
            points.extend((self.hz_x(hz), self.db_y(db)))
        if len(points) >= 4:
            self.canvas.create_line(*points, fill=self.scope_trace, width=2, smooth=True, tags="spectrum")

        if frame.rms_db < -55.0:
            self.note_display.set("—")
            self.base_note.set("I cannot hear a steady note yet")
            self.tuning.set("")
            self.recipe.set("Hold a comfortable “aaa” for a moment")
            self.status.set("Speak closer or check the microphone.")
            self.detail.set("Once the cyan line rises, the app will name your base note and its overtones.")
            return

        if fundamental:
            note = note_for_frequency(fundamental)
            self.note_display.set(note)
            self.base_note.set(f"Base note ≈ {fundamental:.0f} Hz")
            self.tuning.set(tuning_for_frequency(fundamental))
            self.recipe.set(harmonic_note_stack(fundamental))
            self.status.set("One sung note can create this whole stack. Cyan peaks near amber guides are its overtones.")
            for number in range(1, 9):
                harmonic = fundamental * number
                if harmonic > MAX_FREQUENCY:
                    break
                x = self.hz_x(harmonic)
                self.canvas.create_line(x, 48, x, SPECTRUM_HEIGHT - 20, fill=self.scope_harmonics, dash=(3, 5), tags="harmonics")
            noise_detail = " Noise baseline is on." if self.noise_floor is not None else ""
            self.detail.set(
                f"Input level {frame.rms_db:.1f} dBFS · nearest musical-note names for one natural harmonic series, not automatically a chord.{noise_detail}"
            )
        else:
            self.note_display.set("—")
            self.base_note.set("No steady base note yet")
            self.tuning.set("")
            self.recipe.set("Hold one calm “aaa” for about one second")
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
        guidance = "too quiet" if rms_db < -55 else "strong" if rms_db > -12 else "comfortable"
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
