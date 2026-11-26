#!/bin/bash
# Build the Linux desktop shell, its native apps and its script runtimes.
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
export CARGO_PROFILE_DEV_DEBUG="${CARGO_PROFILE_DEV_DEBUG:-0}"

host=$(uname -m)
case "${1:-native}" in
    native) arches=("$host") ;;
    amd64|x86_64) arches=(x86_64) ;;
    arm64|aarch64) arches=(aarch64) ;;
    all) arches=(x86_64 aarch64) ;;
    *) echo "Desktop target must be native, amd64, arm64 or all." >&2; exit 2 ;;
esac

for tool in cargo rustc npm python3; do
    if ! command -v "$tool" >/dev/null; then
        echo "Desktop build requires $tool." >&2
        exit 1
    fi
done

apps=(hello disk-benchmark sysmon tone-lab browser radio recorder speedtest)
cache="$PWD/target/desktop-apps"
for arch in "${arches[@]}"; do (
    case "$arch" in
        x86_64) node_arch=x64; wheel_platform=manylinux_2_35_x86_64 ;;
        aarch64) node_arch=arm64; wheel_platform=manylinux_2_31_aarch64 ;;
        *) echo "Unsupported desktop architecture: $arch." >&2; exit 2 ;;
    esac

    platform="$arch-unknown-linux-gnu"
    triple_args=()
    output=debug
    if [[ "$arch" != "$host" ]]; then
        export CARGO_PROFILE_DEV_INCREMENTAL="${CARGO_PROFILE_DEV_INCREMENTAL:-false}"
        libdir=$(rustc --print target-libdir --target "$platform")
        if ! compgen -G "$libdir/libstd-*.rlib" > /dev/null; then
            echo "Install the Rust standard library for $platform before cross-compiling." >&2
            exit 1
        fi
        linker_var="CARGO_TARGET_${platform^^}_LINKER"
        linker_var=${linker_var//-/_}
        linker=${!linker_var:-$arch-linux-gnu-gcc}
        if ! command -v "$linker" >/dev/null; then
            echo "Install $linker or set $linker_var for the $platform linker." >&2
            exit 1
        fi
        if [[ -z "${!linker_var:-}" ]]; then
            export FLIPCTL_CROSS_ARCH="$arch"
            export FLIPCTL_CROSS_CC="$linker"
            export FLIPCTL_CROSS_SYSROOT="$cache/sysroots/$arch-bookworm"
            cross_driver="$PWD/tools/desktop-cross-gcc.sh"
            "$cross_driver" --prepare "$arch" "$FLIPCTL_CROSS_SYSROOT"
            linker="$cross_driver"
        fi
        export "$linker_var=$linker"
        cc_var="CC_${platform//-/_}"
        export "$cc_var=${!cc_var:-$linker}"
        triple_args=(--target "$platform")
        output="$platform/debug"
    fi

    dest="$cache/$arch"
    mkdir -p "$dest"
    printf '\nBuilding FlipCTL and desktop apps for %s\n' "$platform"

    cargo build --locked --bin flipctl --target-dir "$PWD/target" "${triple_args[@]}"
    binaries=("$PWD/target/$output/flipctl")
    for app in "${apps[@]}"; do
        app_target="$PWD/target"
        case "$arch:$app" in
            "$host:hello"|"$host:disk-benchmark") app_target="$cache" ;;
        esac
        cargo build --locked --manifest-path "apps/$app/Cargo.toml" \
            --target-dir "$app_target" "${triple_args[@]}"
        binaries+=("$app_target/$output/$app-app")
    done

    # Keep x86 and ARM native addons in different module trees.
    node_version=1.17.1
    node_package="@slint-ui/slint-ui-binary-linux-$node_arch-gnu"
    node_modules="$dest/node/node_modules"
    if [[ ! -f "$node_modules/$node_package/package.json" ]]; then
        npm install --prefix "$dest/node" --omit=optional --ignore-scripts --no-save \
            --package-lock=false --no-audit --no-fund "slint-ui@$node_version"
        npm_archive_dir=$(mktemp -d "$dest/node/.addon.XXXXXX")
        trap 'rm -rf -- "$npm_archive_dir"' EXIT
        npm_archive=$(npm pack --silent --pack-destination "$npm_archive_dir" "$node_package@$node_version")
        mkdir -p "$node_modules/${node_package%/*}" "$npm_archive_dir/unpacked"
        tar -xzf "$npm_archive_dir/$npm_archive" -C "$npm_archive_dir/unpacked" --strip-components=1
        mv -T -- "$npm_archive_dir/unpacked" "$node_modules/$node_package"
    fi
    if [[ ! -f "$dest/python/slint-1.17.1b2.dist-info/METADATA" ]]; then
        python3 -m pip install --target "$dest/python" --only-binary=:all: --no-deps \
            --implementation cp --python-version 3.12 --abi abi3 \
            --platform "$wheel_platform" 'slint==1.17.1b2'
    fi

    # Copying binaries by hard link keeps the build cache and install tree usable
    # without duplicating the executables on disk.
    for binary in "${binaries[@]}"; do
        installed="$dest/${binary##*/}"
        if [[ ! "$binary" -ef "$installed" ]]; then
            ln -f -- "$binary" "$installed"
        fi
    done
    printf 'Desktop artifacts are ready in %s\n' "$dest"
)
done
