#!/bin/sh
# Run only within native-host toolchain Containerfile stages. Never run target.
set -eu
source=${1:?usage: build.sh SOURCE_DIRECTORY OUTPUT_DIRECTORY}
output=${2:?usage: build.sh SOURCE_DIRECTORY OUTPUT_DIRECTORY}
mkdir -p "$output"
output=$(CDPATH= cd -- "$output" && pwd)
cd "$source"
export CARGO_BUILD_JOBS=${BUILD_JOBS:-2}
export SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-1748736000}
export RUSTUP_TOOLCHAIN=${RUSTUP_TOOLCHAIN:-1.95.0}
export CARGO_INCREMENTAL=0
# libasound is dynamically linked so the operator can replace the LGPL library.
# The build provides a musl-native libgcc_eh/unwind-safe linker, not glibc libgcc_s.
export RUSTFLAGS="${RUSTFLAGS:-} -C target-feature=-crt-static -C panic=abort --remap-path-prefix=$PWD=/src/sendspin"
target=${TARGET:-x86_64-unknown-linux-musl}
cargo build --locked --release --target "$target" --bin sendspin-rs-cli
install -m 0755 "target/$target/release/sendspin-rs-cli" "$output/sendspin"
