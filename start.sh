#!/bin/bash
set -e

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export PATH="$HOME/.local/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/.local/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

arch=$(uname -m)
desktop="$PWD/target/desktop-apps/$arch"
built_binary=
if [[ "${1:-}" == build ]]; then
    shift
    build_target=native
    if [[ "${1:-}" == --target ]]; then
        if [[ $# -lt 2 ]]; then
            echo "Missing desktop build target after --target." >&2
            exit 2
        fi
        build_target="$2"
        shift 2
    fi
    if [[ $# -ne 0 ]]; then
        case "$arch:$build_target" in
            *:native|*:all|x86_64:amd64|x86_64:x86_64|aarch64:arm64|aarch64:aarch64) ;;
            *) echo "Build the host target as well to launch on $arch." >&2; exit 2 ;;
        esac
    fi
    ./tools/build-desktop.sh "$build_target"
    if [[ $# -eq 0 ]]; then
        exit 0
    fi
    built_binary="$desktop/flipctl"
fi

case "${1:-}" in
    gui|window|desktop) mode=gui; shift ;;
    headless|server|lan) mode=lan; shift ;;
    panel) mode=panel; shift ;;
    -h|--help)
        echo "Usage: ./start.sh [gui|lan|panel] [FlipCTL options]"
        echo "       ./start.sh build [--target native|amd64|arm64|all] [gui|lan] [FlipCTL options]"
        echo "Example: ./start.sh gui --output HDMI-A-1"
        exit 0
        ;;
    *) mode=auto ;;
esac

native_binary() {
    local expected
    case "$arch" in
        x86_64) expected=62 ;;
        aarch64) expected=183 ;;
        *) return 1 ;;
    esac
    [[ -x "$1" ]] && [[ $(od -An -j18 -N2 -tu2 -- "$1") =~ ^[[:space:]]*$expected$ ]]
}

if [[ -n "${FLIPCTL_BIN:-}" ]]; then
    binary="$FLIPCTL_BIN"
elif [[ -n "$built_binary" ]]; then
    binary="$built_binary"
elif native_binary "$desktop/flipctl"; then
    binary="$desktop/flipctl"
elif native_binary ./target/release/flipctl &&
    { ! native_binary ./target/debug/flipctl || [[ ! ./target/debug/flipctl -nt ./target/release/flipctl ]]; }; then
    binary=./target/release/flipctl
elif native_binary ./target/debug/flipctl; then
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

if [[ "$mode" != panel ]]; then
    export FLIPCTL_SOURCE_ROOT="$PWD"
    if command -v pactl >/dev/null; then
        if [[ -z "${FLIPCTL_AUDIO_SINK:-}" ]] && sink=$(pactl get-default-sink 2>/dev/null); then
            export FLIPCTL_AUDIO_SINK="$sink"
        fi
        if [[ -z "${PULSE_SOURCE:-}" ]] && source=$(pactl get-default-source 2>/dev/null); then
            export PULSE_SOURCE="$source"
        fi
    fi
    python_runtime=
    node_runtime=
    python_modules="$PWD/apps/python-runtime"
    if [[ -d "$desktop/python/slint" ]]; then
        python_modules="$python_modules:$desktop/python"
    fi
    if PYTHONPATH="$python_modules${PYTHONPATH:+:$PYTHONPATH}" python3 -c 'import slint' 2>/dev/null; then
        python_runtime="$PWD/apps/python-runtime/AppRun"
    fi
    node_modules="$desktop/node/node_modules"
    if [[ ! -d "$node_modules" ]]; then
        node_modules="$PWD/target/desktop-apps/node/node_modules"
    fi
    if [[ -d "$node_modules/slint-ui" ]] &&
        NODE_PATH="$node_modules" node -e 'require("slint-ui")' 2>/dev/null; then
        node_runtime="$PWD/apps/js-runtime/AppRun"
    fi
    if [[ -n "$python_runtime$node_runtime" ]]; then
        theme_cache="${XDG_CACHE_HOME:-$HOME/.cache}/flipctl/source-ui"
        mkdir -p "$theme_cache"
        theme_stage=$(mktemp "$theme_cache/.theme.XXXXXX")
        if "$binary" --print-theme > "$theme_stage" && [[ -s "$theme_stage" ]]; then
            theme_digest=$(sha256sum < "$theme_stage")
            export FLIPCTL_THEME="$theme_cache/${theme_digest%% *}.slint"
            mv -f -- "$theme_stage" "$FLIPCTL_THEME"
            export FLIPCTL_UI="$PWD"
            if [[ -n "$python_runtime" ]]; then
                export FLIPCTL_SOURCE_PYTHON="$python_runtime"
                export PYTHONPATH="$python_modules${PYTHONPATH:+:$PYTHONPATH}"
                export SLINT_BACKEND="${SLINT_BACKEND:-winit-software}"
            fi
            if [[ -n "$node_runtime" ]]; then
                export FLIPCTL_SOURCE_JS="$node_runtime"
                export FLIPCTL_NODE_MODULES="$node_modules"
            fi
        else
            rm -f -- "$theme_stage"
        fi
    fi
fi

case "$mode" in
    gui) exec "$binary" --wayland --remote "${FLIPCTL_ADDRESS:-127.0.0.1:8899}" "$@" ;;
    panel) exec "$binary" --panel "$@" ;;
    lan) exec "$binary" --headless --remote "${FLIPCTL_ADDRESS:-0.0.0.0:8899}" "$@" ;;
    local-web) exec "$binary" --headless --remote "${FLIPCTL_ADDRESS:-127.0.0.1:8899}" "$@" ;;
esac
