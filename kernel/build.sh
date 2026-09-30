#!/bin/sh
# Native-host kbuild tools cross-compile the guest; never execute guest binaries.
set -eu
if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
    echo "usage: $0 extracted-linux-source output-directory [jobs]" >&2
    exit 2
fi
source_dir=$(CDPATH= cd -- "$1" && pwd)
mkdir -p "$2"
output_dir=$(CDPATH= cd -- "$2" && pwd)
recipe_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$recipe_dir/source.env"
jobs=${3:-2}
case "$jobs" in ''|*[!0-9]*|0) echo "invalid jobs: $jobs" >&2; exit 2;; esac
# Check the supplied tree, not an independently overrideable source version.
actual_version=$(make -s -C "$source_dir" ARCH=x86_64 kernelversion)
[ "$actual_version" = "$KERNEL_VERSION" ] || {
    echo "expected Linux $KERNEL_VERSION, got $actual_version" >&2
    exit 1
}
work=$(mktemp -d "${TMPDIR:-/tmp}/sendspin-kernel.XXXXXX")
trap 'rm -rf "$work"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
export KBUILD_BUILD_USER=sendspin KBUILD_BUILD_HOST=builder KBUILD_BUILD_VERSION=1
export SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-1748736000}
case "$SOURCE_DATE_EPOCH" in ''|*[!0-9]*) echo "invalid SOURCE_DATE_EPOCH" >&2; exit 2;; esac
KBUILD_BUILD_TIMESTAMP=$(date -u -d "@$SOURCE_DATE_EPOCH" '+%Y-%m-%d %H:%M:%S UTC')
export KBUILD_BUILD_TIMESTAMP
cross=${CROSS_COMPILE:-x86_64-linux-gnu-}
make -C "$source_dir" O="$work" ARCH=x86_64 CROSS_COMPILE="$cross" \
    KCONFIG_ALLCONFIG="$recipe_dir/config" allnoconfig
make -C "$source_dir" O="$work" ARCH=x86_64 CROSS_COMPILE="$cross" olddefconfig
# Fail rather than silently losing required features to Kconfig dependencies.
# An absent symbol is equivalent to n, but requested y/value symbols must match.
awk '
    FNR == NR {
        if ($0 ~ /^CONFIG_[A-Za-z0-9_]+=/) {
            split($0, kv, "="); requested[kv[1]] = substr($0, length(kv[1]) + 2)
        }
        next
    }
    /^CONFIG_[A-Za-z0-9_]+=/ {
        split($0, kv, "="); actual[kv[1]] = substr($0, length(kv[1]) + 2)
        if (actual[kv[1]] == "m") { print "unexpected module: " kv[1] > "/dev/stderr"; failed = 1 }
    }
    END {
        for (key in requested) {
            value = (key in actual) ? actual[key] : "n"
            if (value != requested[key]) {
                print key ": requested " requested[key] ", resolved " value > "/dev/stderr"
                failed = 1
            }
        }
        exit failed
    }
' "$recipe_dir/config" "$work/.config"
cp "$work/.config" "$output_dir/kernel.config"
cp "$source_dir/COPYING" "$output_dir/COPYING"
cp "$source_dir/LICENSES/preferred/GPL-2.0" "$output_dir/GPL-2.0"
cp "$source_dir/LICENSES/exceptions/Linux-syscall-note" "$output_dir/Linux-syscall-note"
if [ "${KERNEL_CONFIG_ONLY:-0}" = 1 ]; then
    exit 0
fi
make -C "$source_dir" O="$work" ARCH=x86_64 CROSS_COMPILE="$cross" \
    KCFLAGS="-ffile-prefix-map=$source_dir=/usr/src/linux -ffile-prefix-map=$work=/usr/src/linux-build" \
    -j "$jobs" bzImage
cp "$work/arch/x86/boot/bzImage" "$output_dir/kernel"
