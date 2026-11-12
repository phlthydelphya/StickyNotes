#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Install Dusk Sticky Notes for the current user (no root needed).
# Usage: ./install.sh              install or update
#        ./install.sh --autostart  also reopen open notes at login
#        ./install.sh --uninstall  remove the app (notes in ~/.local/share/dusk-sticky-notes stay)
set -euo pipefail

APP_ID=org.duskos.StickyNotes
SRC=$(cd "$(dirname "$0")" && pwd)
LIB="$HOME/.local/lib/dusk-sticky-notes"
BIN="$HOME/.local/bin/dusk-sticky-notes"
DESKTOP="$HOME/.local/share/applications/$APP_ID.desktop"
ICON="$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/$APP_ID.desktop"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -rf -- "$LIB"
  rm -f -- "$BIN" "$DESKTOP" "$ICON" "$AUTOSTART"
  echo "Removed. Notes kept in ${XDG_DATA_HOME:-$HOME/.local/share}/dusk-sticky-notes"
  exit 0
fi

mkdir -p "$LIB" "$(dirname "$BIN")" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
STAGE="$LIB/.stage-$$"
BACKUP="$LIB/.backup-$$"
SWITCHED=0
HAD_TREE=0
HAD_LAUNCHER=0
rollback() {
  status=$?
  trap - EXIT HUP INT TERM
  if (( status != 0 && SWITCHED )); then
    if [[ -e "$BACKUP/stickynotes" ]]; then
      rm -rf -- "$LIB/stickynotes"
      mv -- "$BACKUP/stickynotes" "$LIB/stickynotes"
    elif (( ! HAD_TREE )); then
      rm -rf -- "$LIB/stickynotes"
    fi
    if [[ -e "$BACKUP/dusk-sticky-notes" ]]; then
      rm -f -- "$LIB/dusk-sticky-notes"
      mv -- "$BACKUP/dusk-sticky-notes" "$LIB/dusk-sticky-notes"
    elif (( ! HAD_LAUNCHER )); then
      rm -f -- "$LIB/dusk-sticky-notes"
    fi
  fi
  rm -rf -- "$STAGE" "$BACKUP"
  exit "$status"
}
trap rollback EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -- "$STAGE" "$BACKUP"
cp -r "$SRC/stickynotes" "$STAGE/"
install -m 755 "$SRC/dusk-sticky-notes" "$STAGE/dusk-sticky-notes"
PYTHONDONTWRITEBYTECODE=1 python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import stickynotes.app' "$STAGE"
find "$STAGE/stickynotes" -name '__pycache__' -prune -exec rm -rf {} +
# Record original presence before moving anything so signal/error rollback
# can restore whichever moves completed.
if [[ -e "$LIB/stickynotes" ]]; then HAD_TREE=1; fi
if [[ -e "$LIB/dusk-sticky-notes" ]]; then HAD_LAUNCHER=1; fi
SWITCHED=1
if (( HAD_TREE )); then mv -- "$LIB/stickynotes" "$BACKUP/stickynotes"; fi
if (( HAD_LAUNCHER )); then mv -- "$LIB/dusk-sticky-notes" "$BACKUP/dusk-sticky-notes"; fi
mv -- "$STAGE/stickynotes" "$LIB/stickynotes"
mv -- "$STAGE/dusk-sticky-notes" "$LIB/dusk-sticky-notes"
ln -sf "$LIB/dusk-sticky-notes" "$BIN"
install -m 644 "$SRC/data/$APP_ID.desktop" "$DESKTOP"
install -m 644 "$SRC/data/$APP_ID.svg" "$ICON"
update-desktop-database -q "$(dirname "$DESKTOP")" 2>/dev/null || true
gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
if [[ "${1:-}" == "--autostart" ]]; then
  mkdir -p "$(dirname "$AUTOSTART")"
  sed -e "s|^Exec=dusk-sticky-notes\$|Exec=$BIN --restore|" \
      -e '/^Actions=/d' -e '/^\[Desktop Action/,$d' "$SRC/data/$APP_ID.desktop" > "$AUTOSTART"
  printf 'X-GNOME-Autostart-enabled=true\nNoDisplay=true\n' >> "$AUTOSTART"
  echo "Autostart enabled: $AUTOSTART"
fi
echo "Installed. Run: dusk-sticky-notes"
