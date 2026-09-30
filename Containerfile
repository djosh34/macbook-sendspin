# All RUN instructions execute native-host tools. Target x86_64 files are data.
ARG RUST_IMAGE=docker.io/library/rust@sha256:6258907abe69656e41cd992e0b705cdcfabcbbe3db374f92ed2d47121282d4a1
ARG GO_IMAGE=docker.io/library/golang@sha256:c423747fbd96fd8f0b1102d947f51f9b266060217478e5f9bf86f145969562ee
FROM ${GO_IMAGE} AS native-go
FROM ${RUST_IMAGE} AS native-tools
ARG SOURCE_DATE_EPOCH=1748736000
ARG BUILD_JOBS=2
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH} BUILD_JOBS=${BUILD_JOBS} \
    RUSTUP_TOOLCHAIN=1.95.0 CARGO_INCREMENTAL=0
# Snapshot packages are native-host packages, including cross compiler/sysroot
# data. No foreign dpkg architecture or target package scripts are used.
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* && \
    printf 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20260901T000000Z/ bookworm main\n' > /etc/apt/sources.list && \
    apt-get -o Acquire::Check-Valid-Until=false update && \
    apt-get install -y --no-install-recommends ca-certificates curl python3 make gcc libc6-dev \
      gcc-x86-64-linux-gnu binutils-x86-64-linux-gnu libc6-dev-amd64-cross \
      flex bison bc libssl-dev libelf-dev xz-utils bzip2 patch pkg-config file && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /src

FROM native-tools AS kernel
COPY kernel /src/kernel
RUN . kernel/source.env && mkdir -p /work/linux/source && \
    curl -fL --retry 3 "$KERNEL_URL" -o /work/linux/archive && \
    printf '%s  %s\n' "$KERNEL_SHA256" /work/linux/archive | sha256sum -c - && \
    tar -xf /work/linux/archive -C /work/linux/source --strip-components=1 && \
    sh kernel/build.sh /work/linux/source /work/kernel "${BUILD_JOBS}" && \
    rm -rf /work/linux/source

FROM native-tools AS musl-toolchain
COPY build/inputs.lock /src/build/inputs.lock
COPY build/assembly-build.sh /src/build/assembly-build.sh
RUN sh build/assembly-build.sh fetch musl /work/musl && \
    sh build/assembly-build.sh musl && \
    rustup target add --toolchain 1.95.0 x86_64-unknown-linux-musl
ENV PATH=/opt/musl/bin:/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER=/opt/musl/bin/musl-rust-linker \
    CC_x86_64_unknown_linux_musl=/opt/musl/bin/musl-gcc \
    AR_x86_64_unknown_linux_musl=x86_64-linux-gnu-ar \
    PKG_CONFIG_ALLOW_CROSS=1 PKG_CONFIG_LIBDIR=/opt/musl/lib/pkgconfig \
    RUSTFLAGS="-C link-arg=-Wl,--dynamic-linker=/lib/ld-musl-x86_64.so.1 -L native=/opt/musl/lib"

FROM musl-toolchain AS player-compiled
COPY player /src/player
RUN sh build/assembly-build.sh fetch alsa /work/alsa && \
    sh build/assembly-build.sh alsa target /opt/musl && \
    sh player/prepare.sh /work/player && \
    sh player/build.sh /work/player/source /work/player && \
    sh build/assembly-build.sh player-root && \
    cd /work/player/source && cargo metadata --locked --format-version 1 \
      --filter-platform x86_64-unknown-linux-musl > /work/player/cargo-metadata.json

FROM player-compiled AS player
COPY licenses /src/licenses
RUN python3 /src/licenses/collect.py --output /work/player/licenses \
      --tree sendspin=/work/player/source --tree alsa=/work/alsa/source \
      --tree musl=/work/musl/source \
      --tree rust-stdlib=/usr/local/rustup/toolchains/1.95.0-$(rustc -vV | sed -n 's/^host: //p')/share/doc/rust \
      --cargo-metadata /work/player/cargo-metadata.json

FROM musl-toolchain AS runtime
COPY runtime /src/runtime
RUN CC=/opt/musl/bin/musl-gcc sh runtime/build.sh target /work/runtime && \
    file /work/runtime/sendspin-appliance && \
    ! x86_64-linux-gnu-readelf -l /work/runtime/sendspin-appliance | grep INTERP

FROM native-tools AS linuxkit
COPY build/inputs.lock /src/build/inputs.lock
COPY build/assembly-build.sh /src/build/assembly-build.sh
COPY --from=native-go /usr/local/go /usr/local/go
ENV PATH=/usr/local/go/bin:/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    CGO_ENABLED=0 GOTOOLCHAIN=local
RUN sh build/assembly-build.sh fetch linuxkit /work/linuxkit && \
    cd /work/linuxkit/source/src/cmd/linuxkit && \
    go build -mod=vendor -trimpath -buildvcs=false -p "${BUILD_JOBS}" \
      -ldflags="-s -w" -o /usr/local/bin/linuxkit .

FROM native-tools AS tests
COPY Containerfile NOTICE Makefile /src/
COPY scripts /src/scripts
COPY build /src/build
COPY kernel /src/kernel
COPY runtime /src/runtime
COPY player /src/player
COPY licenses /src/licenses
COPY tests /src/tests
RUN sh build/assembly-build.sh fetch alsa /work/alsa && \
    sh build/assembly-build.sh alsa native /work/native-alsa && \
    sh tests/run.sh alsa /work/native-alsa && \
    sh tests/run.sh player /work/native-alsa && sh tests/run.sh

FROM linuxkit AS assembly
COPY --from=kernel /work/kernel /work/kernel
COPY --from=kernel /work/linux/archive /work/linux/archive
COPY --from=player /work/player/sendspin /work/player/sendspin
COPY --from=player /work/player/rootfs /work/player/rootfs
COPY --from=player /work/player/licenses /out/licenses
COPY --from=player /work/player/cli.tar.gz /work/player/cli.tar.gz
COPY --from=player /work/player/protocol.crate /work/player/protocol.crate
COPY --from=player /work/alsa/archive /work/alsa/archive
COPY --from=player /work/musl/archive /work/musl/archive
COPY --from=runtime /work/runtime /work/runtime
COPY . /src
RUN python3 licenses/collect.py --output /out/licenses \
      --tree linuxkit=/work/linuxkit/source --tree recipe-licenses=/src/licenses && \
    cp NOTICE /out/licenses/NOTICE && cp LICENSE /out/licenses/project-LICENSE && \
    cp /work/kernel/COPYING /work/kernel/GPL-2.0 /work/kernel/Linux-syscall-note /out/licenses/ && \
    python3 licenses/sources.py --lock build/inputs.lock --output /out/licenses/sources \
      --cache /work/legal-cache --recipe /src \
      --archive linux=/work/linux/archive --archive alsa=/work/alsa/archive \
      --archive musl=/work/musl/archive --archive linuxkit=/work/linuxkit/archive \
      --archive sendspin=/work/player/cli.tar.gz --archive sendspin_protocol=/work/player/protocol.crate && \
    python3 build/assembly.py --lock build/inputs.lock --kernel /work/kernel/kernel \
      --player /work/player/sendspin --runtime /work/runtime/sendspin-appliance \
      --player-root /work/player/rootfs --repo /src --work /work/assembly --out /out && \
    python3 tests/artifacts.py /out --assembly

FROM scratch AS artifacts
COPY --from=assembly /out/kernel /kernel
COPY --from=assembly /out/initrd.img /initrd.img
COPY --from=assembly /out/boot-image.oci.tar /boot-image.oci.tar
COPY --from=assembly /out/licenses /licenses
