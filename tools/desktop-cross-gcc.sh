#!/bin/bash
# Use the pinned Debian 12 C runtime when cross-building the desktop apps.
set -euo pipefail

if [[ "${1:-}" == --prepare ]]; then
    arch=$2
    dest=$3
    if [[ -f "$dest/usr/$arch-linux-gnu/lib/libc.so.6" ]]; then
        exit 0
    fi

    case "$arch" in
        aarch64)
            deb_arch=arm64
            hashes=(
                91936cbbee75771c360ed16513f6e734a25ca5517fe7c3e13dfd7835d7553186
                5e4cf6abf0e89e89c4b3006bb8b7dc47aa831df25d94df9134929b8b968cacb4
                66457f015d16b7d372db6591cff38db0da928362ff2ea1fa75491e28f6904d81
            ) ;;
        x86_64)
            deb_arch=amd64
            hashes=(
                e410f3d2da35bccf757976d38e6a309d4d94d25c0dfde565a9662e3f75951b2c
                197b497ed91056e6c4093b0972296199260424f24943d7108a8124cc7de750d6
                b7ec9f95d20bd4669119201ec6a1ac372189fc222d35f21631bb79b723519293
            ) ;;
        *) echo "No Debian sysroot for $arch." >&2; exit 2 ;;
    esac
    for tool in curl dpkg-deb; do
        if ! command -v "$tool" >/dev/null; then
            echo "Preparing the cross sysroot requires $tool." >&2
            exit 1
        fi
    done
    packages=(
        "libc6-$deb_arch-cross_2.36-8cross1_all.deb"
        "libc6-dev-$deb_arch-cross_2.36-8cross1_all.deb"
        "linux-libc-dev-$deb_arch-cross_6.1.4-1cross1_all.deb"
    )
    mkdir -p -- "${dest%/*}"
    stage=$(mktemp -d "${dest%/*}/.sysroot.XXXXXX")
    trap 'rm -rf -- "$stage"' EXIT
    mkdir -p "$stage/root"
    repo=https://deb.debian.org/debian/pool/main/c/cross-toolchain-base
    for i in "${!packages[@]}"; do
        pkg="$stage/${packages[i]}"
        curl -fsSL --retry 2 "$repo/${packages[i]}" -o "$pkg"
        printf '%s  %s\n' "${hashes[i]}" "$pkg" | sha256sum --check --status
        dpkg-deb -x "$pkg" "$stage/root"
    done
    mv -T -- "$stage/root" "$dest"
    exit 0
fi

prefix="$FLIPCTL_CROSS_SYSROOT/usr/$FLIPCTL_CROSS_ARCH-linux-gnu"
exec "$FLIPCTL_CROSS_CC" --sysroot="$FLIPCTL_CROSS_SYSROOT" \
    -B"$prefix/lib/" -isystem "$prefix/include" -L"$prefix/lib" "$@"
