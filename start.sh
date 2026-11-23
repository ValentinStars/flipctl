#!/bin/bash
set -e

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export PATH="$HOME/.local/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/.local/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

built_binary=
if [[ "${1:-}" == build ]]; then
    shift
    export CARGO_PROFILE_DEV_DEBUG="${CARGO_PROFILE_DEV_DEBUG:-0}"
    cargo build --locked --bin flipctl --target-dir "$PWD/target"
    built_binary="$PWD/target/debug/flipctl"
    app_dir="$PWD/target/desktop-apps"
    mkdir -p "$app_dir/debug"
    for app in hello disk-benchmark sysmon tone-lab browser radio recorder speedtest; do
        case "$app" in
            hello|disk-benchmark) app_target="$app_dir" ;;
            *) app_target="$PWD/target" ;;
        esac
        cargo build --locked --manifest-path "apps/$app/Cargo.toml" --target-dir "$app_target"
        if [[ "$app_target" != "$app_dir" && ! "$app_target/debug/$app-app" -ef "$app_dir/debug/$app-app" ]]; then
            ln -f -- "$app_target/debug/$app-app" "$app_dir/debug/$app-app"
        fi
    done
    if command -v npm >/dev/null; then
        npm install --prefix "$PWD/target/desktop-apps/node" --ignore-scripts --no-save \
            --package-lock=false --no-audit --no-fund slint-ui@1.17.1
    fi
    if [[ $# -eq 0 ]]; then
        exit 0
    fi
fi

case "${1:-}" in
    gui|window|desktop) mode=gui; shift ;;
    headless|server|lan) mode=lan; shift ;;
    panel) mode=panel; shift ;;
    -h|--help)
        echo "Usage: ./start.sh [gui|lan|panel] [FlipCTL options]"
        echo "       ./start.sh build [gui|lan] [FlipCTL options]"
        echo "Example: ./start.sh gui --output HDMI-A-1"
        exit 0
        ;;
    *) mode=auto ;;
esac

if [[ -n "${FLIPCTL_BIN:-}" ]]; then
    binary="$FLIPCTL_BIN"
elif [[ -n "$built_binary" ]]; then
    binary="$built_binary"
elif [[ -x ./target/release/flipctl && ! ./target/debug/flipctl -nt ./target/release/flipctl ]]; then
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
    if python3 -c 'import slint' 2>/dev/null; then
        python_runtime="$PWD/apps/python-runtime/AppRun"
    fi
    node_modules="$PWD/target/desktop-apps/node/node_modules"
    if [[ -d "$node_modules/slint-ui" ]] && command -v node >/dev/null; then
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
                export PYTHONPATH="$PWD/apps/python-runtime${PYTHONPATH:+:$PYTHONPATH}"
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
