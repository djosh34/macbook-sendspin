#!/bin/sh
set -eu
mode=${1:?expected target or native}
out=${2:?expected output directory}
case "$mode" in
    target) compiler=${CC:-x86_64-linux-musl-gcc}; link=-static; headers='-idirafter /usr/x86_64-linux-gnu/include' ;;
    native) compiler=${CC:-cc}; link=; headers= ;;
    *) echo 'runtime: invalid build mode' >&2; exit 2 ;;
esac
mkdir -p "$out"
# Native builds are host binaries; target artifacts are NEVER executed here.
"$compiler" $headers ${CPPFLAGS:-} -std=c11 -O2 -Wall -Wextra -Werror \
    -ffile-prefix-map="$(pwd)"=. -fno-ident $link \
    runtime/config.c runtime/audio.c runtime/linux.c runtime/main.c \
    -Wl,--build-id=none -o "$out/sendspin-appliance"
