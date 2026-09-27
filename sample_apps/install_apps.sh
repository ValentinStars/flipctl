#!/bin/bash
# Install sample apps to ~/Apps
DEST="$HOME/Apps"
mkdir -p "$DEST/Games"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing FlipCTL sample apps to $DEST..."
cp -v "$SRC"/*.fap.py "$DEST/" 2>/dev/null || true
cp -v "$SRC"/*.AppImage "$DEST/" 2>/dev/null || true
cp -v "$SRC"/Games/* "$DEST/Games/" 2>/dev/null || true
chmod +x "$DEST"/*.fap.py "$DEST"/*.AppImage "$DEST"/Games/* 2>/dev/null || true

echo "Done! Apps are ready in $DEST"
