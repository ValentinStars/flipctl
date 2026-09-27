#!/bin/bash
# ==============================================================================
# Universal Starter Script for FlipCTL
# Works on any Linux distribution (Arch, Manjaro, Ubuntu, Debian, Fedora, RPi)
# ==============================================================================

set -e

# Base directory
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$ROOT_DIR/bin"
LOCAL_BIN="$HOME/.local/bin"

# Export local paths
export PATH="$LOCAL_BIN:$PATH"
export LD_LIBRARY_PATH="$HOME/.local/lib:${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# Default configuration: Signature Flipper Amber theme + 2x scaling
export FLIPCTL_AMBER="${FLIPCTL_AMBER:-1}"
export FLIPCTL_SCALE="${FLIPCTL_SCALE:-2}"

echo "================================================================="
echo "               🐬  FlipCTL Universal Launcher  🐬                 "
echo "================================================================="
echo " Backlight theme: $([ "$FLIPCTL_AMBER" = "1" ] && echo 'Flipper Amber (#FF8200)' || echo 'Classic Monochrome')"
echo " Window scale:    ${FLIPCTL_SCALE}x"
echo " Web UI port:     8899"
echo "================================================================="

# Check if flipctl binary is available
FLIPCTL_BIN=""
if [ -x "$LOCAL_BIN/flipctl" ]; then
    FLIPCTL_BIN="$LOCAL_BIN/flipctl"
elif [ -x "$ROOT_DIR/target/release/flipctl" ]; then
    FLIPCTL_BIN="$ROOT_DIR/target/release/flipctl"
elif [ -x "$ROOT_DIR/target/debug/flipctl" ]; then
    FLIPCTL_BIN="$ROOT_DIR/target/debug/flipctl"
else
    echo "Building flipctl binary..."
    cargo build --release --bin flipctl
    FLIPCTL_BIN="$ROOT_DIR/target/release/flipctl"
fi

# Ensure Apps directory exists with starter apps
mkdir -p "$HOME/Apps"

# Check mode
MODE="${1:-gui}"

case "$MODE" in
    headless|server|lan)
        echo "Starting FlipCTL in Headless LAN / Web Mode..."
        echo "Web interface will be available at: http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'localhost'):8899"
        exec "$FLIPCTL_BIN" --listen 0.0.0.0:8899
        ;;
    gui|window|desktop|*)
        # If in an existing Wayland session or nested Sway
        if [ -n "$WAYLAND_DISPLAY" ]; then
            echo "Wayland session detected ($WAYLAND_DISPLAY). Launching FlipCTL window..."
            exec "$FLIPCTL_BIN" --listen 0.0.0.0:8899
        elif [ -x "$LOCAL_BIN/sway" ]; then
            echo "Launching via Sway backend..."
            exec "$LOCAL_BIN/sway" -c <(echo "
                output HEADLESS-1 resolution 512x288
                exec $FLIPCTL_BIN --listen 0.0.0.0:8899
            ")
        else
            echo "Starting FlipCTL..."
            exec "$FLIPCTL_BIN" --listen 0.0.0.0:8899
        fi
        ;;
esac
