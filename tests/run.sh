#!/bin/sh
# Native checks run in Containerfile's tests stage. Target artifacts are bytes only.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo"
mode=${1:-all}
tmp_root=${TEST_TMP_ROOT:-/tmp/macbook-sendspin-tests}
case "$mode" in
    all|runtime|recipe|artifacts|alsa|player|orchestration) ;;
    clean)
        [ ! -L "$tmp_root" ] || { echo 'tests: refuse symlink temporary root' >&2; exit 1; }
        # Explicit recovery for uncatchable termination; do not run concurrently.
        for path in "$tmp_root"/work.*; do
            [ ! -e "$path" ] || rm -rf -- "$path"
        done
        exit 0 ;;
    *) echo 'usage: tests/run.sh [all|runtime|recipe|artifacts OUTPUT_DIR|alsa NATIVE_PREFIX|player NATIVE_PREFIX|orchestration|clean]' >&2; exit 2 ;;
esac
[ ! -L "$tmp_root" ] || { echo 'tests: refuse symlink temporary root' >&2; exit 1; }
mkdir -p -- "$tmp_root"
tmp_root=$(CDPATH= cd -- "$tmp_root" && pwd)
work=$(mktemp -d "$tmp_root/work.XXXXXXXX")
trap 'rm -rf -- "$work"' 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

native_guard() {
    # Even an accidentally overridden toolchain must not execute a target binary.
    python3 - "$1" <<'PY'
import platform, struct, sys
with open(sys.argv[1], 'rb') as binary:
    header = binary.read(20)
expected = {'x86_64': 62, 'aarch64': 183}.get(platform.machine())
if expected is None or header[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', header, 18)[0] != expected:
    sys.exit('tests: refusing to execute non-native test binary')
PY
}
runtime_checks() {
    cc -std=c11 -Wall -Wextra -Werror -Iruntime \
        runtime/config.c runtime/audio.c tests/runtime_test.c -o "$work/runtime-test"
    native_guard "$work/runtime-test"
    (cd "$work" && ./runtime-test)
    sh runtime/build.sh native "$work/native-runtime"
    native_guard "$work/native-runtime/sendspin-appliance"
    status=0
    "$work/native-runtime/sendspin-appliance" > "$work/invalid-mode.log" 2>&1 || status=$?
    [ "$status" -eq 1 ] || { echo 'tests: native launcher did not reject invalid argc' >&2; exit 1; }
    printf '%s\n' 'sendspin: playback disabled: expected fixed prepare or launch mode' > "$work/expected.log"
    cmp "$work/expected.log" "$work/invalid-mode.log"
    echo 'runtime: full native launcher C11/Werror build and safe invalid-argc check passed'
}
alsa_checks() {
    prefix=${2:?alsa requires NATIVE_PREFIX}
    prefix=$(CDPATH= cd -- "$prefix" && pwd)
    [ -e "$prefix/lib/libasound.so" ] || { echo 'tests: native patched libasound.so required' >&2; exit 1; }
    cc -std=c11 -Wall -Wextra -Werror -I"$prefix/include" tests/alsa_pcm_test.c \
        -L"$prefix/lib" -Wl,-rpath,"$prefix/lib" -Wl,--export-dynamic -lasound -ldl \
        -o "$work/alsa-test"
    native_guard "$work/alsa-test"
    ALSA_CONFIG_PATH="$repo/player/alsa.conf" LD_LIBRARY_PATH="$prefix/lib" "$work/alsa-test"
}
player_checks() {
    prefix=${2:?player requires NATIVE_PREFIX}
    prefix=$(CDPATH= cd -- "$prefix" && pwd)
    [ -e "$prefix/lib/libasound.so" ] || { echo 'tests: native patched libasound.so required' >&2; exit 1; }
    export RUSTUP_TOOLCHAIN=1.95.0
    host=$(rustc -vV | sed -n 's/^host: //p')
    case "$(uname -m):$host" in
        x86_64:x86_64-unknown-linux-gnu|aarch64:aarch64-unknown-linux-gnu) ;;
        *) echo 'tests: refusing non-native Rust test toolchain' >&2; exit 1 ;;
    esac
    sh player/prepare.sh "$work/player"
    (
        cd "$work/player/source"
        export PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig" LD_LIBRARY_PATH="$prefix/lib"
        export CARGO_TARGET_DIR="$work/cargo-target" CARGO_BUILD_JOBS=${BUILD_JOBS:-2}
        export CARGO_INCREMENTAL=0 RUSTFLAGS="--remap-path-prefix=$work=/tests"
        cargo test --locked --target "$host" --lib --bin sendspin-rs-cli
    )
}
case "$mode" in
    all)
        runtime_checks
        python3 tests/recipe.py
        sh tests/orchestration.sh "$work/orchestration"
        echo 'tests: generated-output inspection is separate: tests/run.sh artifacts OUTPUT_DIR'
        ;;
    runtime) runtime_checks ;;
    recipe) python3 tests/recipe.py ;;
    artifacts) python3 tests/artifacts.py "${2:?artifacts requires OUTPUT_DIR}" ;;
    alsa) alsa_checks "$@" ;;
    player) player_checks "$@" ;;
    orchestration) sh tests/orchestration.sh "$work/orchestration" ;;
esac
