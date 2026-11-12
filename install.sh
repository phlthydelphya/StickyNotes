#!/usr/bin/env bash
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
rm -rf -- "$LIB/stickynotes"
cp -r "$SRC/stickynotes" "$LIB/"
find "$LIB/stickynotes" -name '__pycache__' -prune -exec rm -rf {} +
install -m 755 "$SRC/dusk-sticky-notes" "$LIB/dusk-sticky-notes"
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
