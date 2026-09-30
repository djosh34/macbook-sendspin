#!/bin/sh
# Host orchestration only: compilation/assembly/tests stay in native Podman stages.
set -eu

cd "$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)"
mode=${1:-}
case "$mode" in build|check|clean) ;; *) echo 'usage: scripts/build.sh build|check|clean' >&2; exit 2 ;; esac

# Keep one stable lock inode so simultaneous build/check/clean calls cannot race.
# Only this tiny lock and the controlled temporary directory survive normal cleanup.
for path in .tmp out; do
    if [ -L "$path" ] || { [ -e "$path" ] && [ ! -d "$path" ]; }; then
        echo "refusing non-directory or symlink: $path" >&2
        exit 1
    fi
done
umask 022
mkdir -p .tmp
if [ -L .tmp/lock ] || { [ -e .tmp/lock ] && [ ! -f .tmp/lock ]; }; then
    echo 'refusing unsafe .tmp/lock' >&2
    exit 1
fi
exec 9>.tmp/lock
flock 9

if [ "$mode" = clean ]; then
    rm -rf -- out
    # Retain the lock inode; remove only this recipe's abandoned work directories.
    find .tmp -mindepth 1 -maxdepth 1 -type d -name 'work.*' -exec rm -rf -- {} +
    exit 0
fi

[ "$(uname -s)" = Linux ] || { echo 'Linux hosts only' >&2; exit 1; }
case "$(uname -m)" in
    aarch64|arm64) arch=arm64 ;;
    x86_64|amd64) arch=amd64 ;;
    *) echo 'only native ARM64 and x86-64 hosts are supported' >&2; exit 1 ;;
esac
podman=${PODMAN:-podman}
epoch=${SOURCE_DATE_EPOCH:-1748736000}
jobs=${BUILD_JOBS:-2}
case "$epoch" in ''|*[!0-9]*) echo 'SOURCE_DATE_EPOCH must be an unsigned integer' >&2; exit 2 ;; esac
case "$jobs" in ''|*[!0-9]*) echo 'BUILD_JOBS must be a positive integer' >&2; exit 2 ;; esac
[ "$jobs" -gt 0 ] || { echo 'BUILD_JOBS must be positive' >&2; exit 2; }
command -v "$podman" >/dev/null 2>&1 || { echo "Podman executable not found: $podman" >&2; exit 1; }

work=$(mktemp -d "$PWD/.tmp/work.XXXXXXXX")
cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    # Restore the preceding complete output if interrupted during publication.
    if [ -d "$work/previous" ] && [ ! -e out ]; then
        mv -- "$work/previous" out || status=1
    fi
    rm -rf -- "$work"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Isolate Podman's implicit environment defaults; the Containerfile receives
# the unchanged explicit epoch build argument for its native build tools.
podman_build() (
    unset SOURCE_DATE_EPOCH
    "$podman" "$@"
)
set -- build --file Containerfile --platform "linux/$arch" \
    --layers --rm --force-rm \
    --build-arg "SOURCE_DATE_EPOCH=$epoch" --build-arg "BUILD_JOBS=$jobs" \
    --iidfile "$work/image.id"
# Podman 5.6 interprets the epoch build argument as --source-date-epoch and
# rejects --timestamp alongside it. Ubuntu 24.04's Podman 4.9 supports only
# --timestamp. Detect capabilities instead of assuming either host version.
podman_help=$(podman_build build --help)
if printf '%s\n' "$podman_help" | grep -Eq -- '[[:space:]]--source-date-epoch([[:space:]=]|$)'; then
    set -- "$@" --source-date-epoch "$epoch"
    if printf '%s\n' "$podman_help" | grep -Eq -- '[[:space:]]--rewrite-timestamp([[:space:]=]|$)'; then
        set -- "$@" --rewrite-timestamp
    fi
else
    set -- "$@" --timestamp "$epoch"
fi
if [ -n "${BUILD_NETWORK:-}" ]; then
    set -- "$@" --network "$BUILD_NETWORK"
fi
if [ "$mode" = check ]; then
    podman_build "$@" --target tests .
    exit 0
fi

podman_build "$@" --target artifacts --output "type=local,dest=$work/export" .
for artifact in kernel initrd.img boot-image.oci.tar; do
    if [ ! -f "$work/export/$artifact" ] || [ ! -s "$work/export/$artifact" ] || [ -L "$work/export/$artifact" ]; then
        echo "missing or invalid exported artifact: $artifact" >&2
        exit 1
    fi
done
if [ ! -d "$work/export/licenses" ] || [ -L "$work/export/licenses" ]; then
    echo 'missing exported licenses directory' >&2
    exit 1
fi
(
    cd "$work/export"
    # Hash licenses too, with stable lexical ordering; generated files stay in out/.
    find kernel initrd.img boot-image.oci.tar licenses -type f -print | \
        LC_ALL=C sort | while IFS= read -r file; do sha256sum "$file"; done > SHA256SUMS
)
if [ -d out ]; then mv -- out "$work/previous"; fi
mv -- "$work/export" out
printf '%s\n' 'Exported out/kernel, out/initrd.img, out/boot-image.oci.tar, out/licenses and out/SHA256SUMS'
