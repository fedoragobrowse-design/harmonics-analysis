#!/bin/sh
set -eu
name=harmonics-analysis
version=1.1.0
stage="build/${name}_${version}_all"
rm -rf "$stage"
mkdir -p "$stage/DEBIAN" "$stage/usr/bin" "$stage/usr/lib/$name" "$stage/usr/share/applications"
cat > "$stage/DEBIAN/control" <<'EOF'
Package: harmonics-analysis
Version: 1.1.0
Section: sound
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-tk, alsa-utils, ffmpeg
Maintainer: Harmonics Analysis
Description: Local voice, harmonic, and sound-file analysis
 A local microphone and sound-file analyser with note, harmonic,
 recording, and voice-colour views.
EOF
install -m 755 harmonic-viewer.py "$stage/usr/lib/$name/harmonic-viewer.py"
printf '%s\n' '#!/bin/sh' 'exec /usr/bin/python3 /usr/lib/harmonics-analysis/harmonic-viewer.py "$@"' > "$stage/usr/bin/harmonics-analysis"
chmod 755 "$stage/usr/bin/harmonics-analysis"
cat > "$stage/usr/share/applications/harmonics-analysis.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Harmonics Analysis
Comment=See your voice harmonics, notes, and sound files
Exec=harmonics-analysis
Icon=audio-input-microphone
Terminal=false
Categories=AudioVideo;
StartupWMClass=Tk
EOF
dpkg-deb --root-owner-group --build "$stage" "${name}_${version}_all.deb"
