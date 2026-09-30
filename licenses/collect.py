#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Retain notices from actual resolved source trees, without target execution."""
import argparse
import hashlib
import json
import re
import shutil
import tomllib
from pathlib import Path

LEGAL = re.compile(r"(?:^|[._-])(licen[cs]e|copying|copyright|notice|authors)(?:$|[._-])", re.I)


def collect_tree(name, source, output, required=True):
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError(f"not a source directory: {source}")
    count = 0
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in {".git", "target", "node_modules", "__pycache__"} for part in relative.parts):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        legal_directory = source.name.lower() in {"licenses", "license"} and path.suffix != ".py"
        if legal_directory or LEGAL.search(path.name) or any(part.lower() in {"licenses", "license"} for part in relative.parts[:-1]):
            dest = output / name / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
            count += 1
    if not count and required:
        raise ValueError(f"no license/notices found for {name}: {source}")
    return count


def collect_fallback(name, package, source, output, lock_packages):
    """Only exact, reviewed crate/workspace exceptions; never generic SPDX text."""
    here = Path(__file__).resolve().parent
    fallbacks = json.loads((here / "fallbacks.json").read_text())
    spec = fallbacks.get(name)
    if not spec:
        raise ValueError(f"no license/notices found for {name}: {source}; explicit pinned fallback required")
    locked = [p for p in lock_packages if p["name"] == package["name"] and
              p["version"] == package["version"] and p.get("source") == package.get("source")]
    if len(locked) != 1 or locked[0].get("checksum") != spec["package_checksum"]:
        raise ValueError(f"pinned license fallback Cargo.lock mismatch: {name}")
    # Modern Cargo registry extraction has no .cargo-checksum.json (vendor does).
    # Verify the actual immutable cached .crate bytes, not invented metadata.
    archive = source.parents[2] / "cache" / source.parent.name / (name + ".crate")
    if not archive.is_file():
        raise ValueError(f"pinned license fallback needs original Cargo cache archive: {archive}")
    hasher = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    checksum = hasher.hexdigest()
    vcs = json.loads((source / ".cargo_vcs_info.json").read_text())
    if (checksum != spec["package_checksum"] or vcs["git"]["sha1"] != spec["source_commit"] or
            vcs.get("path_in_vcs", "") != spec.get("path_in_vcs", "") or
            package.get("license") != spec["license"] or package.get("repository") != spec["repository"] or
            not (package.get("source") or "").startswith("registry+")):
        raise ValueError(f"pinned license fallback identity mismatch: {name}")
    dest = output / name
    dest.mkdir(parents=True, exist_ok=True)
    for filename, expected in spec["files"].items():
        data = (here / "fallbacks" / name / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"pinned license fallback checksum mismatch: {name}/{filename}")
        (dest / filename).write_bytes(data)
    (dest / "provenance.json").write_text(json.dumps(spec, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tree", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--cargo-metadata", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for entry in args.tree:
        name, sep, path = entry.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            parser.error("--tree must be a safe NAME=PATH")
        collect_tree(name, Path(path), args.output)
    if args.cargo_metadata:
        metadata = json.loads(args.cargo_metadata.read_text())
        resolved = {node["id"] for node in metadata["resolve"]["nodes"]}
        lock_packages = tomllib.loads((Path(metadata["workspace_root"]) / "Cargo.lock").read_text())["package"]
        manifest = []
        for package in sorted(metadata["packages"], key=lambda p: (p["name"], p["version"])):
            if package["id"] not in resolved:
                continue
            name = package["name"] + "-" + package["version"]
            source = Path(package["manifest_path"]).parent
            if not collect_tree(name, source, args.output / "rust-dependencies", required=False):
                collect_fallback(name, package, source, args.output / "rust-dependencies", lock_packages)
            manifest.append({key: package.get(key) for key in
                             ("name", "version", "license", "license_file", "authors", "repository", "source")})
        (args.output / "rust-dependencies.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
