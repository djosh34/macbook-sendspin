#!/bin/sh
# Test the host export/cleanup boundary, not mocked kernel/OCI correctness.
# Caller owns this single directory and removes it on success/failure/signals.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
root=${1:?controlled temporary directory required}
[ ! -e "$root" ] || { echo 'orchestration: temporary directory already exists' >&2; exit 1; }
mkdir -p "$root/repo/scripts" "$root/bin" "$root/unrelated"
cp "$repo/Makefile" "$root/repo/"
cp "$repo/scripts/build.sh" "$root/repo/scripts/"
printf untouched > "$root/unrelated/cache"
cat > "$root/bin/podman" <<'EOF'
#!/bin/sh
set -eu
[ "${SOURCE_DATE_EPOCH+x}" != x ] || { echo 'inherited epoch leaked to Podman' >&2; exit 2; }
printf '%s\n' "$@" >> "$MOCK_LOG"
features=${MOCK_FEATURES:-modern}
if [ "$#" -eq 2 ] && [ "$1" = build ] && [ "$2" = --help ]; then
    case "$features" in
        modern) printf '%s\n' '      --source-date-epoch int' '      --rewrite-timestamp' ;;
        source-only) printf '%s\n' '      --source-date-epoch int' ;;
        legacy) printf '%s\n' '      --timestamp int' ;;
        *) exit 2 ;;
    esac
    exit 0
fi
output=; target=; timestamp=; source_epoch=; build_epoch=; rewrite=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --timestamp) timestamp=$2; shift ;;
        --source-date-epoch) source_epoch=$2; shift ;;
        --rewrite-timestamp) rewrite=1 ;;
        --build-arg) case "$2" in SOURCE_DATE_EPOCH=*) build_epoch=${2#SOURCE_DATE_EPOCH=} ;; esac; shift ;;
        --target) target=$2; shift ;;
        --output) output=${2#type=local,dest=}; shift ;;
    esac
    shift
done
# Podman5.6 rejects timestamp+epoch buildarg independent of environment;
# Podman4.9 instead requires its legacy timestamp flag. Source-only versions
# support epoch but not timestamp rewriting. Exercise advertised capability.
case "$features" in
    modern|source-only)
        [ -z "$timestamp" ] || { echo 'incompatible timestamp/epoch options' >&2; exit 2; }
        [ -n "$source_epoch" ] && [ "$source_epoch" = "$build_epoch" ]
        if [ "$features" = modern ]; then [ "$rewrite" = 1 ]; else [ -z "$rewrite" ]; fi ;;
    legacy)
        [ -z "$source_epoch" ] && [ -z "$rewrite" ] || { echo 'unknown modern epoch flags' >&2; exit 2; }
        [ -n "$timestamp" ] && [ "$timestamp" = "$build_epoch" ] ;;
    *) exit 2 ;;
esac
case "${MOCK_MODE:-success}" in
    fail) exit 42 ;;
    term) kill -TERM "$PPID"; exit 143 ;;
    slow) : > "$MOCK_ENTERED"; sleep 1 ;;
esac
if [ "$target" = artifacts ]; then
    mkdir -p "$output/licenses"
    printf kernel > "$output/kernel"
    printf initrd > "$output/initrd.img"
    case "${MOCK_MODE:-success}" in
        missing) ;;
        empty) : > "$output/boot-image.oci.tar" ;;
        directory) mkdir "$output/boot-image.oci.tar" ;;
        symlink) ln -s "$output/kernel" "$output/boot-image.oci.tar" ;;
        *) printf archive > "$output/boot-image.oci.tar" ;;
    esac
    printf notice > "$output/licenses/LICENSE"
fi
EOF
chmod +x "$root/bin/podman"
export PODMAN="$root/bin/podman" MOCK_LOG="$root/calls"
cd "$root/repo"
make build > "$root/success.log" 2>&1
(cd out && sha256sum -c SHA256SUMS > "$root/checksums.log")
no_work() { [ "$(find .tmp -mindepth 1 -maxdepth 1 -type d | wc -l)" -eq 0 ]; }
no_work
cp out/SHA256SUMS "$root/expected"
for mode in fail missing empty directory symlink term; do
    if MOCK_MODE=$mode make build > "$root/$mode.log" 2>&1; then echo "unexpected $mode success" >&2; exit 1; fi
    cmp out/SHA256SUMS "$root/expected"
    no_work
done
# A final rename error must restore the preceding complete export.
cat > "$root/bin/mv" <<'EOF'
#!/bin/sh
case "$*" in *'/export out') exit 17 ;; esac
exec /usr/bin/mv "$@"
EOF
chmod +x "$root/bin/mv"
if PATH="$root/bin:$PATH" make build > "$root/publish.log" 2>&1; then exit 1; fi
cmp out/SHA256SUMS "$root/expected"
no_work
rm "$root/bin/mv"
# Clean waits for the active build; bounded handshake avoids hanging on failure.
MOCK_MODE=slow MOCK_ENTERED="$root/entered" make build > "$root/slow.log" 2>&1 &
producer=$!
tries=0
while [ ! -f "$root/entered" ]; do
    tries=$((tries + 1))
    [ "$tries" -lt 100 ] && kill -0 "$producer" 2>/dev/null || { wait "$producer" || :; exit 1; }
    sleep 0.05
done
make clean > "$root/concurrent-clean.log" 2>&1 &
cleaner=$!
wait "$producer"
wait "$cleaner"
[ ! -e out ]
no_work
make build > "$root/rebuild.log" 2>&1
make check > "$root/check.log" 2>&1
make test > "$root/test.log" 2>&1
# Capability branches use the same public Make contract on both exports/checks.
for features in source-only legacy; do
    MOCK_FEATURES=$features make check > "$root/$features-check.log" 2>&1
    MOCK_FEATURES=$features make build > "$root/$features-build.log" 2>&1
    (cd out && sha256sum -c SHA256SUMS > "$root/$features-checksums.log")
    no_work
done
if BUILD_JOBS=0 make build > "$root/jobs.log" 2>&1; then exit 1; fi
if SOURCE_DATE_EPOCH=oops make build > "$root/epoch.log" 2>&1; then exit 1; fi
mkdir .tmp/work.abandoned
make clean > "$root/clean.log" 2>&1
[ ! -e out ] && [ ! -e .tmp/work.abandoned ] && [ -f .tmp/lock ]
[ "$(cat "$root/unrelated/cache")" = untouched ]
ln -s "$root/unrelated" out
if make clean > "$root/symlink-clean.log" 2>&1; then exit 1; fi
[ -f "$root/unrelated/cache" ]
case "$(uname -m)" in aarch64|arm64) arch=arm64 ;; x86_64|amd64) arch=amd64 ;; *) exit 1 ;; esac
grep -q "^linux/$arch\$" "$MOCK_LOG"
grep -q '^tests$' "$MOCK_LOG"
! grep -q '^run$\|^prune$' "$MOCK_LOG"
printf '%s\n' 'orchestration: modern/source-only/legacy epoch args, export/checksums, cleanup, rollback, locking passed'
