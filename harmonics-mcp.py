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
    ]


def call_tool(name: str, arguments: object) -> dict[str, object]:
    if name == "harmonics_status":
        return text(json.dumps({"version": VIEWER.APP_VERSION, "sample_rate_hz": VIEWER.SAMPLE_RATE, "frame_samples": VIEWER.FRAME_SIZE, "pitch_range_hz": [VIEWER.MIN_PITCH_HZ, VIEWER.MAX_PITCH_HZ]}))
    if name != "analyze_pcm_frame" or not isinstance(arguments, dict):
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
