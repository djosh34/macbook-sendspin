# MacBook Sendspin

Stateless x86-64 LinuxKit appliance: custom kernel, direct ALSA optical output,
100 ms lead time and 20% software volume on each player start. No persisted player
state, root disk, discovery, SSH or guest log files.

## Build

Prerequisites: Linux ARM64 or x86-64, Podman, Make and normal shell utilities;
network access to pinned upstream inputs. Toolchains run inside native-host build
containers; the build does not execute x86-64 guest binaries.

```sh
make check                 # make test is an alias
make build
make clean                 # remove out/ and abandoned .tmp/work.*; retain Podman caches
```

Outputs: `out/kernel`, `out/initrd.img`, `out/boot-image.oci.tar`, `out/licenses/`
and `out/SHA256SUMS`. Defaults: `BUILD_JOBS=2`, `SOURCE_DATE_EPOCH=1748736000`.
If Podman's private-network DNS fails locally, use `make build BUILD_NETWORK=host`.
Temporary work has exit-trap cleanup; use `make clean` for interrupted leftovers.
The tiny `.tmp/lock` is retained for locking.
The OCI boot image contains `/kernel` and `/initrd.img`, owned by UID/GID 107.
There are no publication or deployment targets.

## KubeVirt example

[examples/kubevirt.yaml](examples/kubevirt.yaml) is a **halted**, diskless VM
example using the MacBook's existing HDA device resource. Replace its image
reference with an operator-supplied registry image. Do not assign the controller
to two running guests. Cluster/device setup and rollout are outside this recipe.
VM/VMI server dry-run admission passed on KubeVirt v1.8.4 with `HostDevices`
only; no extra kernelBoot gate. Guest boot/audio still need hardware acceptance.

All four `sendspin.server`, `sendspin.port`, `sendspin.id`, `sendspin.name` boot
arguments are required. Values are percent-decoded once (`%20` for a space);
use a hostname/numeric IP, port 1–65535, stable ASCII ID (letters/digits/`._-`),
and UTF-8 display name. Invalid/missing/duplicate arguments leave playback off
and report over serial. New arguments take effect in a new VM instance, without
rebuilding the image.

With the example's `logSerialConsole: true`, collect serial output from the
KubeVirt launcher pod (requires `kubectl` and cluster access):

```sh
kubectl -n macbook-audio logs -f -l app.kubernetes.io/name=macbook-sendspin -c guest-console-log
```

Log storage/retention belongs to Kubernetes, not the guest.
