# Release and signing guide

## Windows code signing

Every tagged release is configured to require Authenticode signatures on both
`Harmonics-Analysis-Windows.exe` and `Harmonics-Analysis-MCP-Windows.exe`.
The workflow signs with SHA-256, uses a trusted timestamp server, and verifies
the signatures before checksums and release assets are produced.

Before tagging `v1.6.0` or a later version, an organization owner must add two
GitHub Actions secrets to the repository:

- `WINDOWS_CERTIFICATE_BASE64` — base64 of the password-protected OV or EV
  Authenticode `.pfx` certificate issued to the release publisher.
- `WINDOWS_CERTIFICATE_PASSWORD` — the certificate's password.

Never commit a certificate, private key, password, or a base64 certificate
value to this repository. Without both secrets, a tagged workflow fails before
it can publish an unsigned Windows release.

## Safety checks before publishing

1. Run `python3 -m unittest -v` and `./build-deb.sh` locally.
2. Confirm the application and MCP versions match the intended tag.
3. Push the commit, then tag and push `v<version>`.
4. Verify the Windows job completes the MCP startup smoke test and Authenticode
   verification; verify the Debian job produces its checksum sidecar.
5. Download the release assets and compare each SHA-256 sidecar before manual
   installation.

## Runtime recovery

Windows capture first tries 48 kHz then a selected device's native shared-mode
rate. A capture generation prevents stale streams from publishing frames after
a restart. If a live stream stalls for four seconds, the app retries it twice;
otherwise it shows a clear recovery message. Range reports intentionally
require a tightly held vowel for about 0.8 seconds, while the live tuner remains
available for ordinary speech and instruments.

Updates are downloaded to a temporary file, limited to 200 MiB, SHA-256
verified, and atomically promoted only after verification. Windows moves the
old executable to a temporary rollback name before replacing it and restores
it if the replacement move fails. Linux uses only a verified local `.deb` with
APT's `--no-remove` and `--no-download` safeguards; a failed or cancelled
installation leaves the installed application in place.
