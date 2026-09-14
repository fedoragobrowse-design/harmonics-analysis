"""Regression tests for the local MCP score tools."""

import importlib.util
import json
import os
import struct
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("harmonics_mcp", "harmonics-mcp.py")
assert SPEC and SPEC.loader
MCP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MCP)


def midi_file() -> bytes:
    track = bytes((0, 0x90, 60, 100, 96, 0x80, 60, 0, 0, 0x90, 64, 100, 96, 0x80, 64, 0, 0, 0xFF, 0x2F, 0))
    return b"MThd" + struct.pack(">IHHH", 6, 0, 1, 96) + b"MTrk" + struct.pack(">I", len(track)) + track


class McpScoreToolTests(unittest.TestCase):
    def test_score_tools_inspect_and_compare_local_midi(self):
        with tempfile.NamedTemporaryFile(suffix=".mid") as score_file:
            score_file.write(midi_file())
            score_file.flush()
            inspect = MCP.call_tool("inspect_score_file", {"score_path": score_file.name})
            inspect_data = json.loads(inspect["content"][0]["text"])
            self.assertEqual((inspect_data["lowest_note"], inspect_data["highest_note"]), ("C4", "E4"))
            comparison = MCP.call_tool("check_song_range", {"score_path": score_file.name, "observed_midi_notes": [60, 62, 64, 64]})
            comparison_data = json.loads(comparison["content"][0]["text"])
            self.assertEqual(comparison_data["verdict"], "Speech-only indication")

    def test_score_tool_rejects_unexpected_extensions(self):
        response = MCP.call_tool("inspect_score_file", {"score_path": os.path.abspath("README.md")})
        self.assertTrue(response["isError"])
        self.assertIn("MIDI", response["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
