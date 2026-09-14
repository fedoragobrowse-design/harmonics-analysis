# Release guide

## Windows distribution

Tagged releases build `Harmonics-Analysis-Windows.exe` and
`Harmonics-Analysis-MCP-Windows.exe` without Authenticode signing, plus a
SHA-256 sidecar for each file. This avoids a certificate-service failure from
blocking Linux or Windows releases, but Windows may show an “Unknown publisher”
warning. Users should download only from the GitHub Release and verify the
matching checksum before running the file.

An OV or EV Authenticode certificate can be added in a future release workflow
once it is available and tested. Never commit a certificate or private key.

## Safety checks before publishing

1. Run `python3 -m unittest -v` and `./build-deb.sh` locally.
2. Confirm the application and MCP versions match the intended tag.
3. Push the commit, then tag and push `v<version>`.
4. Verify the Windows job completes the MCP startup smoke test and produces
   both checksum sidecars; verify the Debian job produces its checksum sidecar.
5. Download the release assets and compare each SHA-256 sidecar before manual
   installation.

Each release also includes `install-harmonics-analysis.sh`. It copies the
downloaded `.deb` into `/var/tmp` with world-readable file permissions before
calling APT, avoiding the `_apt` sandbox warning that can occur in private home
directories. It uses `apt install --no-remove` and removes its temporary file
when it exits.

## Runtime recovery

Windows capture first tries 48 kHz then a selected device's native shared-mode
rate. A capture generation prevents stale streams from publishing frames after
a restart. If a live stream stalls for four seconds, the app retries it twice;
otherwise it shows a clear recovery message. Range reports intentionally
require a tightly held vowel for about 0.8 seconds. Conversational speech builds
a separate spoken-pitch profile and never assigns a voice type or gender.

Updates are downloaded to a temporary file, limited to 200 MiB, SHA-256
verified, and atomically promoted only after verification. Windows moves the
old executable to a temporary rollback name before replacing it and restores
it if the replacement move fails. Linux uses only a verified local `.deb` with
APT's `--no-remove` and `--no-download` safeguards; a failed or cancelled
installation leaves the installed application in place.
