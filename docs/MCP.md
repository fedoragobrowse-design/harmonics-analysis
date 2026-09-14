# Harmonics Analysis MCP server

Version 1.6.4 includes a dependency-free, local stdio MCP server. It exposes:

- `harmonics_status` — application version and supported audio format
- `analyze_pcm_frame` — pitch, note, confidence, and level for one base64
  encoded 48 kHz, mono, signed-16-bit PCM frame of exactly 4,096 samples

The server does not open a microphone, scan directories, watch files, save
audio, or send network requests. The MCP client supplies the audio frame.

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
