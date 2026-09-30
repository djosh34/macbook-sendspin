#!/usr/bin/env python3
"""Check the tracked build contract; these checks do not prove a guest boot."""
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    root = Path(__file__).resolve().parent.parent
    lock = json.loads((root / "build/inputs.lock").read_text())
    require(lock["schema"] == 1, "unsupported input lock schema")
    digest = re.compile(r"sha256:[0-9a-f]{64}\Z")
    references = set()
    for name, image in lock["images"].items():
        require(digest.fullmatch(image["index_digest"]), f"{name}: invalid index digest")
        pinned = image["platforms"]["amd64"]["digest"] if image.get("target_only") else image["index_digest"]
        require(image["ref"].endswith("@" + pinned), f"{name}: ref/digest mismatch")
        for arch in ("amd64", "arm64"):
            require(digest.fullmatch(image["platforms"][arch]["digest"]), f"{name}: missing {arch} pin")
        if not image.get("target_only"):
            references.add(image["ref"])
    for name, source in lock["sources"].items():
        require(re.fullmatch(r"[0-9a-f]{64}", source["sha256"]), f"{name}: invalid source checksum")
        require(source["url"].startswith("https://"), f"{name}: non-HTTPS source")
        if "commit" in source:
            require(re.fullmatch(r"[0-9a-f]{40}", source["commit"]), f"{name}: invalid source commit")
            require(source["commit"] in source["url"], f"{name}: URL not pinned to commit")
    kernel_pins = {}
    for line in (root / "kernel/source.env").read_text().splitlines():
        words = shlex.split(line, comments=True)
        if not words:
            continue
        require(len(words) == 1 and "=" in words[0], "kernel source pin must be a literal assignment")
        key, value = words[0].split("=", 1)
        require(key not in kernel_pins, f"duplicate kernel pin: {key}")
        kernel_pins[key] = value
    linux = lock["sources"]["linux"]
    for variable, field in (("KERNEL_URL", "url"), ("KERNEL_SHA256", "sha256"), ("KERNEL_VERSION", "version")):
        require(kernel_pins.get(variable) == linux[field], f"{variable} differs from locked Linux source")
    containerfile = (root / "Containerfile").read_text()
    args = {}
    stages = set()
    native_stages = 0
    # Joining Dockerfile continuations lets this validate multiline FROM/ARG.
    for line in re.sub(r"\\\n", " ", containerfile).splitlines():
        words = shlex.split(line, comments=True)
        if not words:
            continue
        op = words[0].upper()
        if op == "ARG" and len(words) == 2 and "=" in words[1]:
            key, value = words[1].split("=", 1)
            args[key] = value
            if "@sha256:" in value:
                require(value in references, f"Containerfile ARG {key} is not in inputs.lock")
        if op != "FROM":
            continue
        operands = words[1:]
        if operands[0].startswith("--platform="):
            require(operands.pop(0) in ("--platform=$BUILDPLATFORM", "--platform=${BUILDPLATFORM}"),
                    "FROM must not select a target architecture for execution")
        image = operands[0]
        if image.startswith("$"):
            image = args.get(image.removeprefix("$").strip("{}"), "")
        require(image == "scratch" or image in stages or image in references,
                f"unlocked or unknown FROM image: {image}")
        if image in references:
            native_stages += 1
        if len(operands) > 1:
            require(len(operands) == 3 and operands[1].upper() == "AS", "malformed FROM")
            stages.add(operands[2])
    require(native_stages > 0, "no native compiler stages")
    require("tests" in stages and "artifacts" in stages, "missing Make build/check targets")
    require(not re.search(r"\b(qemu[-_]\S*|binfmt_misc|tonistiigi/binfmt)\b", containerfile),
            "build recipe enables target emulation")
    for path in sorted(root.rglob("*.sh")):
        if any(part in (".git", ".tmp", "out") for part in path.parts):
            continue
        subprocess.run(["sh", "-n", str(path)], check=True)
    kernel = (root / "kernel/config").read_text()
    codecs = set(re.findall(r"^CONFIG_(SND_HDA_CODEC_[A-Z0-9_]+)=y$", kernel, re.M))
    require("SND_HDA_CODEC_CIRRUS" in codecs, "Cirrus optical codec not built in")
    require(codecs <= {"SND_HDA_CODEC_CIRRUS"}, "unrelated HDA codecs enabled")
    require("CONFIG_SND_HDA_INTEL=y" in kernel, "HDA PCI controller not built in")
    for path in (root / "NOTICE", root / "licenses"):
        require(path.exists(), "license notice inputs missing")
    print("recipe: locked native FROM/source pins, matching kernel source.env, stages, shell syntax, Cirrus-only HDA")
    print("recipe: structural checks passed; target artifacts were not executed")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"recipe: FAIL: {error}", file=sys.stderr)
        sys.exit(1)
