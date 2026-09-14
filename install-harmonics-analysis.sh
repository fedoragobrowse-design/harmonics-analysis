#!/usr/bin/env bash
# Install a downloaded Harmonics Analysis Debian package without weakening APT's sandbox.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
package_path=${1:-}

if [[ -z "$package_path" ]]; then
  packages=("$script_dir"/harmonics-analysis_*_all.deb)
  if [[ ${#packages[@]} -ne 1 || ! -f "${packages[0]}" ]]; then
    echo "Usage: $0 /path/to/harmonics-analysis_VERSION_all.deb" >&2
    exit 2
  fi
  package_path=${packages[0]}
fi

if [[ ! -f "$package_path" ]]; then
  echo "Package not found: $package_path" >&2
  exit 2
fi

stage_path=$(mktemp /var/tmp/harmonics-analysis-install.XXXXXX.deb)
cleanup() { rm -f -- "$stage_path"; }
trap cleanup EXIT

# APT's unprivileged _apt user can read /var/tmp but may not traverse Downloads.
install -m 0644 -- "$package_path" "$stage_path"
sudo apt install --no-remove "$stage_path"
