# Harmonics Analysis MCP server

Version 2.0.0 includes a dependency-free, local stdio MCP server. It exposes:

- `harmonics_status` — application version and supported audio format
- `analyze_pcm_frame` — pitch, note, confidence, and level for one base64
  encoded 48 kHz, mono, signed-16-bit PCM frame of exactly 4,096 samples
- `inspect_score_file` — local MIDI/MuseScore note count and written pitch span
- `check_song_range` — compare a score's written span with supplied observed
  MIDI notes; this is never a voice-type or gender decision
- `open_harmonics_app` — explicitly launch the desktop app, optionally opening
  one local MIDI/MuseScore score

The server does not start microphone recording, scan directories, watch files,
save audio, or send network requests. Score tools only open the exact local
path supplied by the client, accept MIDI/MuseScore extensions only, and reject
files over 32 MiB. The MCP client supplies any audio frame and observed notes.

## Codex configuration

Linux after installing the `.deb`:

```json
{
  "mcpServers": {
    "harmonics-analysis": {
      "command": "harmonics-analysis-mcp"
    }
  }
}
```

Windows after downloading `Harmonics-Analysis-MCP-Windows.exe`:

```json
{
  "mcpServers": {
    "harmonics-analysis": {
      "command": "C:\\path\\to\\Harmonics-Analysis-MCP-Windows.exe"
    }
  }
}
```

Use the `Harmonics-Analysis-MCP-Windows.exe.sha256` file from the same GitHub
Release to verify the Windows server download first.
