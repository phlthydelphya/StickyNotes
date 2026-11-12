#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Install Dusk Sticky Notes for the current user (no root needed).
# Usage: ./install.sh [--autostart] [--no-shortcut]
#          install or update; --autostart reopens notes at login,
#          --no-shortcut skips the global Ctrl+Alt+N new-note shortcut
#        ./install.sh --uninstall
#          remove the app, its autostart entry and shortcut
#          (notes in ~/.local/share/dusk-sticky-notes stay)
# Autostart and the shortcut can be changed later in the app's Preferences.
set -euo pipefail

AUTOSTART_OPT=0
SHORTCUT_OPT=1
UNINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --autostart) AUTOSTART_OPT=1 ;;
    --no-shortcut) SHORTCUT_OPT=0 ;;
    --uninstall) UNINSTALL=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

APP_ID=org.duskos.StickyNotes
SRC=$(cd "$(dirname "$0")" && pwd)
LIB="$HOME/.local/lib/dusk-sticky-notes"
BIN="$HOME/.local/bin/dusk-sticky-notes"
DESKTOP="$HOME/.local/share/applications/$APP_ID.desktop"
ICON="$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg"
SYMBOLIC_ICON="$HOME/.local/share/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/$APP_ID.desktop"

# Same code the Preferences switches use (stickynotes/integration.py).
integrate() {  # integrate <lib-dir> <python statements using `i`> [arg -> sys.argv[3]]
  python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); from stickynotes import integration as i; exec(sys.argv[2])' "$@"
}

if (( UNINSTALL )); then
  if [[ -d "$LIB/stickynotes" ]]; then
    integrate "$LIB" 'i.set_autostart(False); i.set_shortcut(False)' || true
  fi
  rm -rf -- "$LIB"
  rm -f -- "$BIN" "$DESKTOP" "$ICON" "$SYMBOLIC_ICON" "$AUTOSTART"
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
install -D -m 644 "$SRC/data/$APP_ID-symbolic.svg" "$SYMBOLIC_ICON"
update-desktop-database -q "$(dirname "$DESKTOP")" 2>/dev/null || true
gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
if (( AUTOSTART_OPT )); then
  if integrate "$LIB" "i.set_autostart(True, sys.argv[3])" "$BIN"; then
    echo "Autostart enabled: $AUTOSTART"
  else
    echo "warning: could not enable autostart; turn it on in Preferences" >&2
  fi
fi
# The new version is in place from here: a desktop-settings failure below must
# warn, not make the EXIT trap roll back a working install.
# Fresh install: turn the shortcut on unless --no-shortcut. Upgrade: keep the
# choice made in Preferences and only refresh the command of an enabled one.
if (( HAD_TREE )); then
  integrate "$LIB" "i.shortcut_enabled() and i.set_shortcut(True, sys.argv[3]); i.autostart_enabled() and i.set_autostart(True, sys.argv[3])" "$BIN" \
    || echo "warning: could not refresh the shortcut or autostart entry" >&2
elif (( SHORTCUT_OPT )); then
  if integrate "$LIB" "sys.exit(0 if i.set_shortcut(True, sys.argv[3]) else 1)" "$BIN"; then
    echo "Shortcut: Ctrl+Alt+N creates a new note"
  else
    echo "Shortcut skipped: GNOME media-keys settings not available" >&2
  fi
fi
echo "Installed. Run: dusk-sticky-notes"
