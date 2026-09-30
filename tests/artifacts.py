#!/usr/bin/env python3
"""Inspect exported bytes only. Never extract, boot, or execute target code."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import posixpath
import stat
import struct
import sys
import tarfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def newc(data):
    """Read newc archives, including concatenated archives and zero padding."""
    if data.startswith(b"\x1f\x8b"):
        data = gzip.decompress(data)
    files = {}
    pos = 0
    while pos < len(data):
        if data[pos] == 0:
            pos += 1
            continue
        require(data[pos:pos + 6] in (b"070701", b"070702"), "initramfs is not newc")
        header = data[pos:pos + 110]
        require(len(header) == 110, "truncated cpio header")
        fields = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
        mode, size, name_size = fields[1], fields[6], fields[11]
        pos += 110
        require(name_size > 0 and pos + name_size <= len(data), "invalid cpio name")
        raw_name = data[pos:pos + name_size]
        require(raw_name.endswith(b"\0"), "unterminated cpio name")
        name = raw_name[:-1].decode("utf-8")
        while name.startswith("./"):
            name = name[2:]
        name = name.lstrip("/")
        require(".." not in name.split("/"), "unsafe cpio path")
        pos = (pos + name_size + 3) & ~3
        require(pos + size <= len(data), "truncated cpio content")
        payload = data[pos:pos + size]
        pos = (pos + size + 3) & ~3
        if name != "TRAILER!!!":
            files[name] = (mode, payload)
    require(files, "empty initramfs")
    return files


def executable(files, name):
    require(name in files, f"initramfs missing {name}")
    mode, data = files[name]
    require(stat.S_ISREG(mode) and mode & 0o111, f"{name} is not executable")
    return data


def elf_dependencies(elf, name):
    require(len(elf) >= 64 and elf[:6] == b"\x7fELF\x02\x01", f"{name} is not ELF64 little-endian")
    require(struct.unpack_from("<H", elf, 18)[0] == 62, f"{name} is not x86-64")
    offset = struct.unpack_from("<Q", elf, 32)[0]
    entry_size, count = struct.unpack_from("<HH", elf, 54)
    loads, dynamic, interpreter = [], None, None
    for i in range(count):
        program = offset + i * entry_size
        require(entry_size >= 56 and program + 56 <= len(elf), f"{name}: truncated program header")
        kind, _, start, address, _, size, _, _ = struct.unpack_from("<IIQQQQQQ", elf, program)
        require(start + size <= len(elf), f"{name}: truncated segment")
        if kind == 1:
            loads.append((address, start, size))
        elif kind == 2:
            dynamic = elf[start:start + size]
        elif kind == 3:
            interpreter = elf[start:start + size].rstrip(b"\0").decode()
    if not dynamic:
        return interpreter, []
    needed, strings = [], None
    for position in range(0, len(dynamic) - 15, 16):
        tag, value = struct.unpack_from("<QQ", dynamic, position)
        if tag == 0:
            break
        if tag == 1:
            needed.append(value)
        elif tag == 5:
            strings = value
    if not needed:
        return interpreter, []
    require(strings is not None, f"{name}: missing dynamic string table")
    for address, start, size in loads:
        if address <= strings < address + size:
            strings = start + strings - address
            break
    else:
        raise ValueError(f"{name}: string table not in a load segment")
    names = []
    for index in needed:
        end = elf.find(b"\0", strings + index)
        require(end >= strings + index, f"{name}: unterminated dependency")
        dependency = elf[strings + index:end].decode()
        require(dependency and "/" not in dependency, f"{name}: unsafe dependency")
        names.append(dependency)
    return interpreter, names


def elf_closure(files, initial, prefix):
    pending, visited = [initial], set()
    while pending:
        name = pending.pop()
        # Resolve symlinks as the sandbox loader would, relative to its own root.
        links = set()
        while True:
            require(name in files, f"sandbox ELF closure missing {name}")
            require(name not in links and name.startswith(prefix), "unsafe or cyclic sandbox symlink")
            links.add(name)
            mode, data = files[name]
            if not stat.S_ISLNK(mode):
                break
            target = data.decode()
            relative = prefix + target.lstrip("/") if target.startswith("/") else posixpath.join(posixpath.dirname(name), target)
            name = posixpath.normpath(relative)
        if name in visited:
            continue
        visited.add(name)
        interpreter, needed = elf_dependencies(data, name)
        if interpreter:
            pending.append(prefix + interpreter.lstrip("/"))
        for dependency in needed:
            candidates = [prefix + directory + dependency for directory in ("lib/", "usr/lib/")]
            match = next((candidate for candidate in candidates if candidate in files), None)
            require(match is not None, f"{name}: missing sandbox dependency {dependency}")
            pending.append(match)


def initramfs_checks(data):
    files = newc(data)
    require("init" in files, "initramfs missing /init")
    init_mode, _ = files["init"]
    require(stat.S_ISLNK(init_mode) or init_mode & 0o111, "/init is not executable or a link")
    executable(files, "bin/rc.init")
    wrapper = executable(files, "usr/bin/sendspin-appliance")
    interpreter, needed = elf_dependencies(wrapper, "usr/bin/sendspin-appliance")
    require(not interpreter and not needed, "root runtime wrapper must be static")
    player = "appliance-root/usr/bin/sendspin"
    executable(files, player)
    require("usr/bin/sendspin" not in files, "player duplicated outside its sandbox")
    elf_closure(files, player, "appliance-root/")
    inittab = files.get("etc/inittab", (0, b""))[1].decode()
    entries = set(line.strip() for line in inittab.splitlines() if not line.startswith("#"))
    require("::sysinit:/bin/rc.init" in entries, "native rc.init boot entry missing")
    require("::respawn:/usr/bin/sendspin-appliance --launch" in entries,
            "fixed native player respawn entry missing")
    require("::respawn:/sbin/udhcpc -f -i eth0 -s /etc/udhcpc/sendspin.script" in entries,
            "native foreground DHCP entry missing")
    require(not any(token in inittab for token in ("getty", "syslogd", "/run/log", "/var/log")),
            "guest login or log-file service enabled")
    require(not any(p.startswith("containers/services/") for p in files),
            "unexpected container service supervision")
    print(f"artifacts: initramfs ({len(files)} entries), x86-64 player/runtime, native supervision")


def oci_checks(archive, kernel, initrd):
    with tarfile.open(archive, "r:*") as outer:
        entries = {}
        for member in outer.getmembers():
            name = member.name.removeprefix("./")
            require(name not in entries, f"duplicate OCI archive entry: {name}")
            entries[name] = member

        def read(name):
            require(name in entries and entries[name].isfile(), f"missing OCI regular file {name}")
            return outer.extractfile(entries[name]).read()

        def descriptor(desc):
            require(desc.get("digest", "").startswith("sha256:"), "OCI digest must use sha256")
            digest = desc["digest"].split(":", 1)[1]
            require(len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), "invalid OCI digest")
            data = read("blobs/sha256/" + digest)
            require(len(data) == desc["size"] and sha256(data) == digest, "OCI descriptor digest/size mismatch")
            return data

        require(json.loads(read("oci-layout"))["imageLayoutVersion"] == "1.0.0", "OCI layout version")
        index = json.loads(read("index.json"))
        require(index["schemaVersion"] == 2 and len(index["manifests"]) == 1, "expected one OCI image")
        manifest = json.loads(descriptor(index["manifests"][0]))
        require(manifest["schemaVersion"] == 2, "OCI manifest schema")
        config = json.loads(descriptor(manifest["config"]))
        require(config["architecture"] == "amd64" and config["os"] == "linux", "OCI target is not linux/amd64")
        require(config.get("config", {}).get("User") in ("107", "107:107"), "KubeVirt OCI user must be 107")
        require(config["rootfs"]["type"] == "layers", "OCI rootfs type")
        require(len(config["rootfs"]["diff_ids"]) == len(manifest["layers"]), "OCI diff-id count")
        payloads = {}
        for layer_desc, diff_id in zip(manifest["layers"], config["rootfs"]["diff_ids"]):
            layer = descriptor(layer_desc)
            uncompressed = gzip.decompress(layer) if layer.startswith(b"\x1f\x8b") else layer
            require(diff_id == "sha256:" + sha256(uncompressed), "OCI uncompressed diff-id mismatch")
            with tarfile.open(fileobj=io.BytesIO(uncompressed), mode="r:") as tar:
                for member in tar:
                    name = member.name.removeprefix("./").lstrip("/")
                    require(".." not in name.split("/"), "unsafe OCI layer path")
                    if member.isdir():
                        continue
                    require(name in ("kernel", "initrd.img"), f"unexpected boot image payload: {name}")
                    require(name not in payloads and member.isfile(), "boot payload must be unique regular file")
                    require(member.uid == 107 and member.gid == 107, "boot payload ownership must be 107:107")
                    require(member.mode & 0o444 == 0o444 and member.mode & 0o022 == 0,
                            "boot payload mode must be readable and not group/world writable")
                    payloads[name] = tar.extractfile(member).read()
        require(payloads == {"kernel": kernel, "initrd.img": initrd}, "OCI boot bytes differ from exports")
    print("artifacts: OCI descriptors/diff-ids, linux/amd64, uid 107, exact boot payload bytes")


def main():
    require(len(sys.argv) in (2, 3) and (len(sys.argv) == 2 or sys.argv[2] == "--assembly"),
            "usage: artifacts.py OUTPUT_DIR [--assembly]")
    assembly = len(sys.argv) == 3
    out = Path(sys.argv[1])
    kernel = (out / "kernel").read_bytes()
    initrd = (out / "initrd.img").read_bytes()
    require(len(kernel) >= 0x238 and kernel[0x1fe:0x200] == b"\x55\xaa" and
            kernel[0x202:0x206] == b"HdrS", "kernel is not x86 bzImage")
    require(struct.unpack_from("<H", kernel, 0x236)[0] & 1,
            "kernel lacks x86-64 boot capability")
    initramfs_checks(initrd)
    oci_checks(out / "boot-image.oci.tar", kernel, initrd)
    licenses = out / "licenses"
    require(licenses.is_dir() and any(p.is_file() and p.stat().st_size for p in licenses.rglob("*")),
            "upstream licenses/notices were not exported")
    sums = out / "SHA256SUMS"
    if sums.exists():
        found = set()
        core = {"kernel", "initrd.img", "boot-image.oci.tar"}
        for line in sums.read_text().splitlines():
            digest, name = line.split(maxsplit=1)
            name = name.removeprefix("*")
            path = Path(name)
            require(not path.is_absolute() and path.as_posix() == name and
                    all(part not in (".", "..") for part in name.split("/")), "unsafe checksum path")
            require(name in core or name.startswith("licenses/"), "unexpected checksum entry")
            require(name not in found, f"duplicate checksum entry: {name}")
            payload = out / path
            require(payload.is_file() and not payload.is_symlink(), "checksum target is not a regular file")
            require(all(not parent.is_symlink() for parent in payload.parents), "symlink in checksum path")
            require(len(digest) == 64 and sha256(payload.read_bytes()) == digest, f"checksum mismatch: {name}")
            found.add(name)
        require(core <= found, "incomplete boot payload SHA256SUMS")
        expected = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and p != sums}
        require(not any(p.is_symlink() for p in out.rglob("*")), "symlink in exported output")
        require(found == expected or (assembly and found == core), "SHA256SUMS does not cover every exported file")
        if assembly and found == core:
            print("artifacts: assembly checksum covers boot files; final Make manifest must also cover licenses")
    else:
        require(assembly, "final export missing SHA256SUMS")
        print("artifacts: SHA256SUMS not yet generated (assembly-stage export)")
    print("artifacts: exported boot payload and license checks passed; no target code executed")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, EOFError, struct.error, tarfile.TarError) as error:
        print(f"artifacts: FAIL: {error}", file=sys.stderr)
        sys.exit(1)
