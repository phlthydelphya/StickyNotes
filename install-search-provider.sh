#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Show notes in GNOME's Activities search. GNOME Shell reads search providers
# only from system data dirs, so this one step needs root:
#   sudo ./install-search-provider.sh               # install
#   sudo ./install-search-provider.sh --uninstall   # remove
# Run ./install.sh first (as your user): it installs the app and the D-Bus
# service that lets GNOME Shell start Sticky Notes for a search.
set -euo pipefail

NAME=org.duskos.StickyNotes.search-provider.ini
DIR=/usr/local/share/gnome-shell/search-providers
SRC=$(cd "$(dirname "$0")" && pwd)/data/$NAME

if (( EUID != 0 )); then
  echo "Run as root: sudo $0 ${1:-}" >&2
  exit 1
fi
case "${1:-}" in
  --uninstall)
    rm -f -- "$DIR/$NAME"
    echo "Removed $DIR/$NAME"
    ;;
  "")
    install -D -m 644 -- "$SRC" "$DIR/$NAME"
    echo "Installed $DIR/$NAME"
    echo "Log out and back in so GNOME Shell loads it."
    ;;
  *)
    echo "Unknown option: $1" >&2
    exit 2
    ;;
esac
