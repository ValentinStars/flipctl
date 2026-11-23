#!/bin/bash
set -e

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export PATH="$HOME/.local/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/.local/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

case "${1:-}" in
    gui|window|desktop) mode=gui; shift ;;
    headless|server|lan) mode=lan; shift ;;
    panel) mode=panel; shift ;;
    -h|--help)
        echo "Usage: ./start.sh [gui|lan|panel] [FlipCTL options]"
        echo "Example: ./start.sh gui --output HDMI-A-1"
        exit 0
        ;;
    *) mode=auto ;;
esac

if [[ -n "${FLIPCTL_BIN:-}" ]]; then
    binary="$FLIPCTL_BIN"
elif [[ -x ./target/release/flipctl ]]; then
    binary=./target/release/flipctl
elif [[ -x ./target/debug/flipctl ]]; then
    binary=./target/debug/flipctl
elif command -v cargo >/dev/null; then
    cargo build --locked --release --bin flipctl
    binary=./target/release/flipctl
else
    echo "Install Rust (see README.md) to build FlipCTL, or set FLIPCTL_BIN." >&2
    exit 1
fi

mkdir -p "$HOME/Apps"

if [[ "$mode" == auto ]]; then
    if [[ -n "${WAYLAND_DISPLAY:-}${WAYLAND_SOCKET:-}" ]]; then
        mode=gui
    else
        mode=local-web
    fi
fi

case "$mode" in
    gui) exec "$binary" --wayland --remote "${FLIPCTL_ADDRESS:-127.0.0.1:8899}" "$@" ;;
    panel) exec "$binary" --panel "$@" ;;
    lan) exec "$binary" --headless --remote "${FLIPCTL_ADDRESS:-0.0.0.0:8899}" "$@" ;;
    local-web) exec "$binary" --headless --remote "${FLIPCTL_ADDRESS:-127.0.0.1:8899}" "$@" ;;
esac
