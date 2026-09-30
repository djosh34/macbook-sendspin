#!/bin/sh
# Native-host Containerfile helpers. Never execute a target binary.
set -eu
command=${1:?command required}
case "$command" in
fetch)
    name=${2:?input name}; work=${3:?work directory}
    mkdir -p "$work/source"
    python3 - "$name" "$work/archive" <<'PY'
import hashlib, json, pathlib, sys, urllib.request
spec = json.loads(pathlib.Path('/src/build/inputs.lock').read_text())['sources'][sys.argv[1]]
dest = pathlib.Path(sys.argv[2])
with urllib.request.urlopen(spec['url'], timeout=120) as source, dest.open('wb') as out:
    digest = hashlib.sha256()
    while block := source.read(1024 * 1024):
        digest.update(block)
        out.write(block)
if digest.hexdigest() != spec['sha256']:
    dest.unlink()
    raise SystemExit('source checksum mismatch: ' + sys.argv[1])
PY
    tar -xf "$work/archive" --strip-components=1 -C "$work/source"
    ;;
musl)
    cd /work/musl/source
    CC=x86_64-linux-gnu-gcc CROSS_COMPILE=x86_64-linux-gnu- \
      ./configure --target=x86_64-linux-musl --prefix=/opt/musl \
      --syslibdir=/opt/musl/lib --enable-shared
    make -j"$BUILD_JOBS"
    make install
    # Rust musl dynamic mode implicitly asks for GCC's shared unwinder. Debian's
    # cross libgcc_s depends on glibc; use Rust's bundled musl libunwind instead.
    cat > /opt/musl/bin/musl-rust-linker <<'SH'
#!/bin/bash
set -e
args=()
for arg do
  if [ "$arg" = -lgcc_s ]; then
    args+=("$(rustc --print sysroot)/lib/rustlib/x86_64-unknown-linux-musl/lib/self-contained/libunwind.a")
  else
    args+=("$arg")
  fi
done
exec /opt/musl/bin/musl-gcc "${args[@]}"
SH
    chmod 0755 /opt/musl/bin/musl-rust-linker
    ;;
alsa)
    mode=${2:?native or target}; prefix=${3:?prefix}
    cd /work/alsa/source
    patch --batch --fuzz=0 -p1 < /src/player/alsa-pcm-only.patch
    if [ "$mode" = target ]; then
      CC=/opt/musl/bin/musl-gcc AR=x86_64-linux-gnu-ar RANLIB=x86_64-linux-gnu-ranlib \
      CFLAGS='-O2 -idirafter /usr/x86_64-linux-gnu/include' \
        ./configure --build="$(gcc -dumpmachine)" --host=x86_64-linux-musl --prefix="$prefix" \
        --with-configdir=/usr/share/alsa --disable-static --enable-shared \
        --disable-python --disable-topology --disable-aload
    elif [ "$mode" = native ]; then
      ./configure --prefix="$prefix" --with-configdir=/usr/share/alsa \
        --disable-static --enable-shared --disable-python --disable-topology --disable-aload
    else
      echo "invalid ALSA mode" >&2; exit 1
    fi
    make -j"$BUILD_JOBS"
    make install
    ;;
player-root)
    root=/work/player/rootfs
    mkdir -p "$root/lib" "$root/usr/bin" "$root/usr/share/alsa" \
      "$root/dev/snd" "$root/etc/network" "$root/oldroot"
    cp /work/player/sendspin "$root/usr/bin/sendspin"
    cp /opt/musl/lib/libc.so "$root/lib/libc.so"
    cp -L /opt/musl/lib/libasound.so.2 "$root/lib/libasound.so.2"
    ln -s libc.so "$root/lib/ld-musl-x86_64.so.1"
    ln -s /etc/network/resolv.conf "$root/etc/resolv.conf"
    cp /src/player/alsa.conf "$root/usr/share/alsa/alsa.conf"
    chmod 0755 "$root/usr/bin/sendspin"
    x86_64-linux-gnu-readelf -l "$root/usr/bin/sendspin" | grep '/lib/ld-musl-x86_64.so.1'
    ! x86_64-linux-gnu-readelf --version-info "$root/usr/bin/sendspin" | grep GLIBC
    ! x86_64-linux-gnu-readelf --version-info "$root/lib/libasound.so.2" | grep GLIBC
    ;;
*) echo "unknown build command: $command" >&2; exit 1 ;;
esac
