#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Native, daemonless OCI assembly. Target files are data, never subprocesses."""
import argparse
import datetime
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request

OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_LAYER = "application/vnd.oci.image.layer.v1.tar"
OCI_LAYER_GZIP = OCI_LAYER + "+gzip"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
KERNEL_REF = "docker.io/sendspin/kernel:local"
APP_REF = "docker.io/sendspin/application:local"


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def created(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def blob(cache, data, media_type):
    checksum = digest(data)
    path = Path(cache) / "blobs/sha256" / checksum.split(":")[1]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("OCI cache blob content mismatch")
    else:
        path.write_bytes(data)
    return {"mediaType": media_type, "digest": checksum, "size": len(data)}


def add_descriptor(cache, descriptor, ref):
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    index_path = cache / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {
        "schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": []}
    descriptor = dict(descriptor)
    descriptor["annotations"] = {"org.opencontainers.image.ref.name": ref}
    descriptor["platform"] = {"architecture": "amd64", "os": "linux"}
    index["manifests"] = [item for item in index["manifests"]
                          if item.get("annotations", {}).get("org.opencontainers.image.ref.name") != ref]
    index["manifests"].append(descriptor)
    index["manifests"].sort(key=lambda item: item["annotations"]["org.opencontainers.image.ref.name"])
    index_path.write_bytes(json_bytes(index))
    (cache / "oci-layout").write_bytes(json_bytes({"imageLayoutVersion": "1.0.0"}))


def tar_entry(name, mode, epoch, uid=0, gid=0):
    if name.startswith("/") or ".." in Path(name).parts:
        raise ValueError("unsafe layer path")
    entry = tarfile.TarInfo(name)
    entry.mode, entry.mtime, entry.uid, entry.gid = mode, epoch, uid, gid
    entry.uname = entry.gname = ""
    return entry


def tree_tar(root, epoch, uid=0, gid=0):
    """Canonical sorted POSIX tar; ignore host uid/gid/mtime and special files."""
    root = Path(root)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
            name = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                entry = tar_entry(name, 0o777, epoch, uid, gid)
                entry.type, entry.linkname = tarfile.SYMTYPE, os.readlink(path)
                archive.addfile(entry)
            elif stat.S_ISDIR(info.st_mode):
                entry = tar_entry(name, 0o755, epoch, uid, gid)
                entry.type = tarfile.DIRTYPE
                archive.addfile(entry)
            elif stat.S_ISREG(info.st_mode):
                entry = tar_entry(name, 0o755 if info.st_mode & 0o111 else 0o644, epoch, uid, gid)
                entry.size = info.st_size
                with path.open("rb") as source:
                    archive.addfile(entry, source)
            else:
                raise ValueError("special file not allowed in input root: " + name)
    return buffer.getvalue()


def add_image(cache, ref, rootfs, epoch, user=None):
    layer = tree_tar(rootfs, epoch)
    # LinuxKit v1.8.2's vendored validate.Image unconditionally gunzips cache
    # layers, even though OCI permits uncompressed tar. Use deterministic gzip.
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=0) as stream:
        stream.write(layer)
    layer_desc = blob(cache, compressed.getvalue(), OCI_LAYER_GZIP)
    config = {"architecture": "amd64", "os": "linux", "created": created(epoch),
              "config": {}, "rootfs": {"type": "layers", "diff_ids": [digest(layer)]}}
    if user is not None:
        config["config"]["User"] = user
    config_desc = blob(cache, json_bytes(config), OCI_CONFIG)
    manifest = {"schemaVersion": 2, "mediaType": OCI_MANIFEST,
                "config": config_desc, "layers": [layer_desc]}
    descriptor = blob(cache, json_bytes(manifest), OCI_MANIFEST)
    add_descriptor(cache, descriptor, ref)
    return descriptor


def write_oci_archive(kernel, initrd, output, epoch):
    """Write an OCI layout tar with only uid107 kernel/initrd boot payloads."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="boot-oci-", dir=output.parent) as temporary:
        temporary = Path(temporary)
        payload = temporary / "payload"
        payload.mkdir()
        for name, source in (("kernel", kernel), ("initrd.img", initrd)):
            shutil.copyfile(source, payload / name)
            (payload / name).chmod(0o644)
        cache = temporary / "oci"
        # Layer ownership must be 107, not just config.User.
        layer = tree_tar(payload, epoch, uid=107, gid=107)
        layer_desc = blob(cache, layer, OCI_LAYER)
        config = {"architecture": "amd64", "os": "linux", "created": created(epoch),
                  "config": {"User": "107:107"},
                  "rootfs": {"type": "layers", "diff_ids": [digest(layer)]}}
        config_desc = blob(cache, json_bytes(config), OCI_CONFIG)
        manifest = {"schemaVersion": 2, "mediaType": OCI_MANIFEST,
                    "config": config_desc, "layers": [layer_desc]}
        desc = blob(cache, json_bytes(manifest), OCI_MANIFEST)
        add_descriptor(cache, desc, "sendspin-boot:latest")
        staged = temporary / "boot-image.oci.tar"
        with staged.open("wb") as destination:
            destination.write(tree_tar(cache, epoch))
        os.replace(staged, output)


def fetch_pinned_init(cache, ref):
    """Pull the locked amd64 leaf/config/layers as bytes; never unpack or run it."""
    match = re.fullmatch(r"docker\.io/(linuxkit/init)@(sha256:[0-9a-f]{64})", ref)
    if not match:
        raise ValueError("LinuxKit init must be a pinned docker.io/linuxkit/init leaf digest")
    repository, checksum = match.groups()
    token_url = "https://auth.docker.io/token?service=registry.docker.io&scope=repository:" + repository + ":pull"
    with urllib.request.urlopen(token_url, timeout=60) as response:
        token = json.load(response)["token"]
    headers = {"Authorization": "Bearer " + token, "Accept": OCI_MANIFEST}
    base = "https://registry-1.docker.io/v2/" + repository

    def download(expected, kind):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected):
            raise ValueError("invalid OCI digest")
        path = Path(cache) / "blobs/sha256" / expected.split(":")[1]
        if path.exists():
            data = path.read_bytes()
        else:
            request = urllib.request.Request(base + "/" + kind + "/" + expected, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
        if digest(data) != expected:
            raise ValueError("registry OCI digest mismatch: " + expected)
        return data

    manifest_bytes = download(checksum, "manifests")
    manifest = json.loads(manifest_bytes)
    if manifest.get("mediaType") != OCI_MANIFEST or manifest.get("schemaVersion") != 2:
        raise ValueError("init reference must resolve to an OCI image manifest, not an index")
    for descriptor in [manifest["config"], *manifest["layers"]]:
        data = download(descriptor["digest"], "blobs")
        if len(data) != descriptor["size"]:
            raise ValueError("registry OCI blob size mismatch")
        blob(cache, data, descriptor["mediaType"])
    config = json.loads(download(manifest["config"]["digest"], "blobs"))
    if config.get("architecture") != "amd64" or config.get("os") != "linux":
        raise ValueError("init OCI image must be linux/amd64 on every build host")
    descriptor = blob(cache, manifest_bytes, OCI_MANIFEST)
    add_descriptor(cache, descriptor, ref)


def install_file(source, destination, mode=0o644):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(mode)


def stage_application(args, application):
    sandbox = application / "appliance-root"
    shutil.copytree(args.player_root, sandbox, symlinks=True)
    for name, mode in (("usr/bin/sendspin", 0o755),):
        install_file(args.player, sandbox / name, mode)
    install_file(args.repo / "player/alsa.conf", sandbox / "usr/share/alsa/alsa.conf")
    for name in ("etc/network", "dev/snd", "oldroot"):
        (sandbox / name).mkdir(parents=True, exist_ok=True)
    resolver = sandbox / "etc/resolv.conf"
    if resolver.exists() or resolver.is_symlink():
        resolver.unlink()
    resolver.symlink_to("/etc/network/resolv.conf")
    # The wrapper is static. Keep the player's libc/ALSA closure exclusively in
    # the sandbox; never overwrite LinuxKit init's independently pinned loader.
    install_file(args.runtime, application / "usr/bin/sendspin-appliance", 0o755)
    for source, destination in (("inittab", "etc/inittab"),
                                ("010-sendspin-prepare", "etc/init.d/010-sendspin-prepare"),
                                ("sendspin.script", "etc/udhcpc/sendspin.script")):
        install_file(args.repo / "runtime" / source, application / destination,
                     0o644 if source == "inittab" else 0o755)
    if not (args.out / "licenses").is_dir():
        raise ValueError("Containerfile must populate /out/licenses before assembly")
    # Corresponding-source archives stay in exported legal output, not guest RAM.
    shutil.copytree(args.out / "licenses", application / "usr/share/licenses", symlinks=False,
                    ignore=lambda directory, names: {"sources"} if Path(directory) == args.out / "licenses" else set())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("lock", "kernel", "player", "runtime", "player-root", "repo", "work", "out"):
        parser.add_argument("--" + option, required=True, type=Path)
    parser.add_argument("--linuxkit", default="/usr/local/bin/linuxkit")
    args = parser.parse_args()
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "1748736000"))
    if not 0 <= epoch <= 0xFFFFFFFF:
        raise ValueError("SOURCE_DATE_EPOCH outside cpio timestamp range")
    args.repo = args.repo.resolve(strict=True)
    args.work.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    lock = json.loads(args.lock.read_text())
    init_ref = lock["images"]["linuxkit_init"]["ref"]
    with tempfile.TemporaryDirectory(prefix="assembly-", dir=args.work) as temporary:
        temporary = Path(temporary)
        cache, kernel_root, app_root = [temporary / name for name in ("cache", "kernel-root", "app-root")]
        kernel_root.mkdir()
        app_root.mkdir()
        install_file(args.kernel, kernel_root / "kernel")
        add_image(cache, KERNEL_REF, kernel_root, epoch)
        stage_application(args, app_root)
        add_image(cache, APP_REF, app_root, epoch)
        fetch_pinned_init(cache, init_ref)
        recipe = temporary / "assembly.yml"
        template = (args.repo / "build/assembly.yml").read_text()
        if template.count("@INIT_REF@") != 1:
            raise ValueError("assembly.yml must contain exactly one init-ref placeholder")
        recipe.write_text(template.replace("@INIT_REF@", init_ref))
        products = temporary / "products"
        products.mkdir()
        subprocess.run([args.linuxkit, "build", "--arch", "amd64", "--cache", str(cache),
                        "--no-sbom", "--format", "kernel+initrd", "--dir", str(products),
                        "--name", "appliance", str(recipe)], check=True)
        kernel, initrd = products / "appliance-kernel", products / "appliance-initrd.img"
        if kernel.read_bytes() != args.kernel.read_bytes():
            raise ValueError("LinuxKit changed kernel payload")
        if not initrd.stat().st_size:
            raise ValueError("LinuxKit produced an empty initramfs")
        write_oci_archive(kernel, initrd, products / "boot-image.oci.tar", epoch)
        for source, name in ((kernel, "kernel"), (initrd, "initrd.img"),
                             (products / "boot-image.oci.tar", "boot-image.oci.tar")):
            install_file(source, args.out / name)
        (args.out / "SHA256SUMS").write_text("".join(
            digest((args.out / name).read_bytes()).split(":")[1] + "  " + name + "\n"
            for name in ("kernel", "initrd.img", "boot-image.oci.tar")))


if __name__ == "__main__":
    main()
