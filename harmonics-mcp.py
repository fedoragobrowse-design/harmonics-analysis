#!/usr/bin/env python3
"""Small stdio MCP server for local Harmonics Analysis pitch inspection.

It deliberately accepts PCM supplied by the MCP client instead of opening a
microphone or arbitrary filesystem paths. Audio therefore stays local and the
server has no network or file-watch dependencies.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
import sys
from typing import Any


def load_viewer() -> Any:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harmonic-viewer.py")
    specification = importlib.util.spec_from_file_location("harmonic_viewer", path)
    if specification is None or specification.loader is None:
        raise RuntimeError("Could not load Harmonics Analysis.")
    module = importlib.util.module_from_spec(specification)
    sys.modules["harmonic_viewer"] = module
    specification.loader.exec_module(module)
    return module


VIEWER = load_viewer()


def result(request_id: object, payload: dict[str, object]) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def error(request_id: object, code: int, message: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def text(value: str) -> dict[str, object]:
    return {"content": [{"type": "text", "text": value}]}


def tools() -> list[dict[str, object]]:
    return [
        {
            "name": "harmonics_status",
            "description": "Return the local Harmonics Analysis version and analysis settings.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "analyze_pcm_frame",
            "description": "Analyse one 4096-sample, 48 kHz, mono signed-16-bit PCM frame supplied as base64. No audio is saved.",
            "inputSchema": {
                "type": "object",
                "properties": {"pcm_s16le_base64": {"type": "string", "description": "Exactly 8192 bytes after base64 decoding."}},
                "required": ["pcm_s16le_base64"],
                "additionalProperties": False,
            },
        },
        {
            "name": "inspect_score_file",
            "description": "Read a local MIDI (.mid/.midi) or MuseScore (.mscz/.mscx) file and return its written pitch span plus practical anchor notes to sing. The file is parsed locally and never uploaded.",
            "inputSchema": {
                "type": "object",
                "properties": {"score_path": {"type": "string", "description": "Absolute or relative path to one local MIDI or MuseScore file."}},
                "required": ["score_path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "check_song_range",
            "description": "Compare a local MIDI/MuseScore file's written range with recorded or otherwise measured MIDI notes. This is a pitch-range check, not a voice-type, gender, technique, or comfort judgement.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "score_path": {"type": "string", "description": "Absolute or relative path to one local MIDI or MuseScore file."},
                    "observed_midi_notes": {"type": "array", "description": "At least three observed note numbers (0–127), usually from a voice take.", "items": {"type": "integer", "minimum": 0, "maximum": 127}, "minItems": 3, "maxItems": 4096},
                },
                "required": ["score_path", "observed_midi_notes"],
                "additionalProperties": False,
            },
        },
        {
            "name": "open_harmonics_app",
            "description": "Open the local Harmonics Analysis desktop app, optionally with one local MIDI/MuseScore score ready to inspect. It never starts microphone recording.",
            "inputSchema": {
                "type": "object",
                "properties": {"score_path": {"type": "string", "description": "Optional local MIDI or MuseScore file to open in the app."}},
                "additionalProperties": False,
            },
        },
    ]


def checked_score_path(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("score_path must be a non-empty local path.")
    path = os.path.abspath(os.path.expanduser(value))
    if os.path.splitext(path)[1].lower() not in (".mid", ".midi", ".mscz", ".mscx"):
        raise ValueError("Choose a MIDI (.mid/.midi) or MuseScore (.mscz/.mscx) file.")
    if not os.path.isfile(path):
        raise ValueError("The score file does not exist or is not a regular file.")
    if os.path.getsize(path) > 32 * 1024 * 1024:
        raise ValueError("Refusing score files larger than 32 MiB.")
    return path


def score_payload(score: object) -> dict[str, object]:
    assert isinstance(score, VIEWER.ScoreAnalysis)
    return {
        "source_name": score.source_name,
        "format": score.format_name,
        "pitched_note_count": len(score.notes),
        "track_count": score.track_count,
        "lowest_note": VIEWER.note_for_midi(score.lowest_midi) if score.lowest_midi is not None else None,
        "highest_note": VIEWER.note_for_midi(score.highest_midi) if score.highest_midi is not None else None,
        "practice_notes": list(VIEWER.score_practice_notes(score)),
        "caution": "The result uses written pitched notes. Multi-part scores or accompaniment can widen the span.",
    }


def launch_app(score_path: str | None) -> None:
    viewer_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harmonic-viewer.py")
    if getattr(sys, "frozen", False):
        extension = ".exe" if os.name == "nt" else ""
        candidate = os.path.join(os.path.dirname(sys.executable), f"Harmonics Analysis{extension}")
        if not os.path.isfile(candidate):
            raise RuntimeError("Could not find the Harmonics Analysis desktop application beside this MCP server.")
        command = [candidate]
    else:
        command = [sys.executable, viewer_path]
    if score_path is not None:
        command.append(score_path)
    subprocess.Popen(command, close_fds=os.name != "nt", start_new_session=os.name != "nt")


def call_tool(name: str, arguments: object) -> dict[str, object]:
    if name == "harmonics_status":
        return text(json.dumps({"version": VIEWER.APP_VERSION, "sample_rate_hz": VIEWER.SAMPLE_RATE, "frame_samples": VIEWER.FRAME_SIZE, "pitch_range_hz": [VIEWER.MIN_PITCH_HZ, VIEWER.MAX_PITCH_HZ]}))
    if not isinstance(arguments, dict):
        return {"content": [{"type": "text", "text": "Unknown tool or invalid arguments."}], "isError": True}
    try:
        if name == "inspect_score_file":
            return text(json.dumps(score_payload(VIEWER.load_score_file(checked_score_path(arguments.get("score_path"))))))
        if name == "check_song_range":
            path = checked_score_path(arguments.get("score_path"))
            midi_notes = arguments.get("observed_midi_notes")
            if not isinstance(midi_notes, list) or not 3 <= len(midi_notes) <= 4096 or any(not isinstance(note, int) or not 0 <= note <= 127 for note in midi_notes):
                raise ValueError("observed_midi_notes must contain 3–4096 integer MIDI notes from 0 to 127.")
            take = VIEWER.TakeAnalysis.start(0)
            take.speech_frequencies.extend(VIEWER.frequency_for_midi(note) for note in midi_notes)
            verdict, explanation = VIEWER.score_singability(VIEWER.load_score_file(path), take)
            return text(json.dumps({"verdict": verdict, "explanation": explanation}))
        if name == "open_harmonics_app":
            optional_path = arguments.get("score_path")
            path = checked_score_path(optional_path) if optional_path is not None else None
            launch_app(path)
            return text(json.dumps({"opened": True, "score_path": path, "message": "Harmonics Analysis was launched locally; microphone recording remains under the user's control."}))
    except (OSError, ValueError, RuntimeError) as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    if name != "analyze_pcm_frame":
        return {"content": [{"type": "text", "text": "Unknown tool or invalid arguments."}], "isError": True}
    try:
        raw = base64.b64decode(arguments["pcm_s16le_base64"], validate=True)
    except (KeyError, ValueError, TypeError) as exc:
        return {"content": [{"type": "text", "text": f"Invalid PCM base64: {exc}"}], "isError": True}
    if len(raw) != VIEWER.FRAME_SIZE * 2:
        return {"content": [{"type": "text", "text": f"Expected {VIEWER.FRAME_SIZE * 2} PCM bytes, got {len(raw)}."}], "isError": True}
    frame = VIEWER.analyze(raw)
    payload = {"rms_dbfs": round(frame.rms_db, 2), "fundamental_hz": round(frame.fundamental_hz, 2) if frame.fundamental_hz else None, "note": VIEWER.note_for_frequency(frame.fundamental_hz) if frame.fundamental_hz else None, "pitch_confidence": round(frame.pitch_confidence, 3)}
    return text(json.dumps(payload))


def handle(message: dict[str, object]) -> dict[str, object] | None:
    method, request_id = message.get("method"), message.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return result(request_id, {"protocolVersion": "2024-11-05", "serverInfo": {"name": "harmonics-analysis", "version": VIEWER.APP_VERSION}, "capabilities": {"tools": {}}})
    if method == "tools/list":
        return result(request_id, {"tools": tools()})
    if method == "tools/call":
        params = message.get("params", {})
        if not isinstance(params, dict):
            return error(request_id, -32602, "params must be an object")
        return result(request_id, call_tool(str(params.get("name", "")), params.get("arguments", {})))
    return error(request_id, -32601, f"Unsupported method: {method}")


def main() -> None:
    for line in sys.stdin:
        try:
            response = handle(json.loads(line))
            if response is not None:
                print(json.dumps(response), flush=True)
        except Exception as exc:
            print(json.dumps(error(None, -32603, str(exc))), flush=True)


if __name__ == "__main__":
    main()
