"""Portable regression tests for pitch and Windows capture helpers."""

import importlib.util
import math
import os
import struct
import sys
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("harmonic_viewer", "harmonic-viewer.py")
assert SPEC and SPEC.loader
VIEWER = importlib.util.module_from_spec(SPEC)
sys.modules["harmonic_viewer"] = VIEWER
SPEC.loader.exec_module(VIEWER)


def vowel_frame(frequency: float) -> bytes:
    samples = []
    for index in range(VIEWER.FRAME_SIZE):
        value = sum(math.sin(2 * math.pi * frequency * harmonic * index / VIEWER.SAMPLE_RATE) / harmonic for harmonic in range(1, 6))
        samples.append(round(11_000 * value))
    return struct.pack(f"<{VIEWER.FRAME_SIZE}h", *samples)


class FakeSoundDevice:
    def query_devices(self, _device, _kind):
        return {"default_samplerate": 44_100}


class FakeResponse:
    def __init__(self, content):
        self.content = content
        self.position = 0

    def read(self, size=-1):
        if size < 0:
            size = len(self.content) - self.position
        result = self.content[self.position:self.position + size]
        self.position += len(result)
        return result

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class PitchRegressionTests(unittest.TestCase):
    def test_about_page_text_includes_current_version_and_privacy(self):
        text = VIEWER.about_text()
        self.assertIn("v1.5.7", text)
        self.assertIn("never uploaded", text)

    def test_harmonic_pitch_handles_low_and_high_vowels(self):
        for expected in (65.4, 82.4, 110.0, 220.0, 440.0, 880.0, 1_100.0):
            frame = VIEWER.analyze(vowel_frame(expected))
            self.assertIsNotNone(frame.fundamental_hz)
            self.assertLess(abs(frame.fundamental_hz - expected), expected * 0.025)
            self.assertGreaterEqual(frame.pitch_confidence, 0.42)

    def test_range_ignores_short_speech_like_pitch_changes(self):
        take = VIEWER.TakeAnalysis.start(10)
        take.sustained_frequencies.extend((110.0, 110.0, 110.0, 110.0, 110.0))
        label, _detail = VIEWER.vocal_range_label(take)
        self.assertEqual(label, "Low observed register")

    def test_instrument_range_never_uses_a_vocal_label(self):
        take = VIEWER.TakeAnalysis.start(10)
        take.sustained_frequencies.extend((82.4, 110.0, 220.0, 329.6, 440.0))
        label, detail = VIEWER.instrument_range_label(take)
        self.assertEqual(label, "Instrument range")
        self.assertIn("No vocal classification", detail)

    def test_windows_uses_native_rate_after_48khz(self):
        self.assertEqual(VIEWER.windows_capture_candidates(FakeSoundDevice(), 4), [48_000.0, 44_100.0])

    def test_pcm_resampling_produces_analysis_rate_block(self):
        raw = struct.pack("<4h", -1000, -500, 500, 1000)
        self.assertEqual(len(VIEWER.resample_pcm_16le(raw, 24_000)), 16)

    def test_pitch_lock_smooths_jitter_but_allows_large_range_changes(self):
        lock = VIEWER.PitchLock()
        self.assertAlmostEqual(lock.update(220.0, 0.9), 220.0)
        self.assertAlmostEqual(lock.update(224.0, 0.9), 224.0)
        self.assertAlmostEqual(lock.update(218.0, 0.9), 220.0)
        self.assertAlmostEqual(lock.update(440.0, 0.9), 440.0)

    def test_windows_update_requires_a_newer_release_and_checksum_asset(self):
        release = {
            "tag_name": "v1.5.0",
            "assets": [
                {"name": "Harmonics-Analysis-Windows.exe", "browser_download_url": "https://example.test/app.exe"},
                {"name": "Harmonics-Analysis-Windows.exe.sha256", "browser_download_url": "https://example.test/app.exe.sha256"},
            ],
        }
        update = VIEWER.select_update(release, "1.2.0", windows=True)
        self.assertEqual(update, VIEWER.UpdateInfo("1.5.0", "https://example.test/app.exe", "https://example.test/app.exe.sha256"))
        self.assertIsNone(VIEWER.select_update({**release, "prerelease": True}, "1.2.0", windows=True))

    def test_verified_download_rejects_bad_data_and_writes_good_data_atomically(self):
        payload = b"new Windows or Debian package"
        checksum = __import__("hashlib").sha256(payload).hexdigest().encode() + b"  app"
        responses = iter((FakeResponse(checksum), FakeResponse(payload)))
        with tempfile.TemporaryDirectory() as directory:
            destination = os.path.join(directory, "app")
            VIEWER.download_verified("asset", "checksum", destination, opener=lambda *_args, **_kwargs: next(responses))
            with open(destination, "rb") as output:
                self.assertEqual(output.read(), payload)
            self.assertEqual(os.stat(destination).st_mode & 0o777, 0o644)

    def test_linux_updater_uses_restricted_polkit_command(self):
        original_which = VIEWER.shutil.which
        VIEWER.shutil.which = lambda name: {"pkexec": "/usr/bin/pkexec", "apt-get": "/usr/bin/apt-get"}.get(name)
        try:
            self.assertEqual(VIEWER.linux_update_command("/tmp/harmonics-analysis_1.5.6_all.deb"), ["/usr/bin/pkexec", "/usr/bin/apt-get", "install", "-y", "--no-remove", "--no-download", "/tmp/harmonics-analysis_1.5.6_all.deb"])
            with self.assertRaises(RuntimeError):
                VIEWER.linux_update_command("/tmp/other-package.deb")
        finally:
            VIEWER.shutil.which = original_which

    def test_linux_update_staging_directory_is_public_temp_space(self):
        self.assertIn(VIEWER.linux_update_staging_directory(), ("/var/tmp", tempfile.gettempdir()))


if __name__ == "__main__":
    unittest.main()
