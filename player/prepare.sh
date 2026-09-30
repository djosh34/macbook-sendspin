#!/bin/sh
# Fetch upstream, verify content, apply the tracked appliance patches.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
work=${1:?usage: prepare.sh WORK_DIRECTORY}
mkdir -p "$work"
cd "$work"
curl -fL --retry 3 'https://codeload.github.com/s3than/sendspin-rs-cli/tar.gz/63749008fb868bf72a16e9dcb21072fae30284c9' -o cli.tar.gz
printf '%s  %s\n' dae793d4fbca6796d090bd86804407636d040b3cfbf1bdecaa47ea5b59a75a5e cli.tar.gz | sha256sum -c -
mkdir source
tar -xzf cli.tar.gz -C source --strip-components=1
curl -fL --retry 3 https://static.crates.io/crates/sendspin/sendspin-0.3.7.crate -o protocol.crate
printf '%s  %s\n' 385582e65769c399069c12d81b65322040d31eba500935542cf143f9ebe8e7f4 protocol.crate | sha256sum -c -
mkdir source/protocol
tar -xzf protocol.crate -C source/protocol --strip-components=1
patch --batch --fuzz=0 -d source -p1 < "$here/appliance.patch"
patch --batch --fuzz=0 -d source/protocol -p1 < "$here/protocol.patch"
cp "$here/Cargo.lock" source/Cargo.lock
# No dormant filesystem persistence or discovery source in the appliance tree.
rm -f source/src/config.rs source/src/mdns.rs source/rust-toolchain.toml
# Original upstream config/CPAL integration tests do not describe this build.
rm -rf source/tests
