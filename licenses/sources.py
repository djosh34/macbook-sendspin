#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Export hash-verified corresponding sources beside redistributed binaries."""
import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def add_bytes(archive, name, data, executable=False):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o755 if executable else 0o644
    info.mtime = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    archive.addfile(info, io.BytesIO(data))


def filtered_archive(source, destination, includes):
    # Keep Alpine's exact build recipes/configuration/patches, not unrelated aports.
    with source.open("rb") as raw, tarfile.open(fileobj=raw, mode="r:*") as upstream:
        with destination.open("wb") as dest, gzip.GzipFile(fileobj=dest, mode="wb", filename="", mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as output:
                found = set()
                for member in sorted(upstream.getmembers(), key=lambda m: m.name):
                    parts = PurePosixPath(member.name).parts
                    if len(parts) < 2 or ".." in parts or member.name.startswith("/"):
                        continue
                    relative = "/".join(parts[1:])
                    matches = {p for p in includes if relative == p or relative.startswith(p + "/")}
                    if not matches or not member.isfile():
                        continue
                    found.update(matches)
                    with upstream.extractfile(member) as stream:
                        add_bytes(output, relative, stream.read(), bool(member.mode & 0o111))
                if found != set(includes):
                    raise ValueError(f"missing source paths: {set(includes) - found}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--recipe", type=Path)
    parser.add_argument("--archive", action="append", default=[], metavar="NAME=PATH",
                        help="reuse an already downloaded upstream archive (hash still checked)")
    args = parser.parse_args()
    provided = {}
    for entry in args.archive:
        name, sep, path = entry.partition("=")
        if not sep or not name or name in provided:
            parser.error("--archive requires a unique NAME=PATH")
        provided[name] = Path(path)
    lock = json.loads(args.lock.read_text())
    if provided.keys() - lock["sources"].keys():
        parser.error("--archive names must exist in inputs.lock sources")
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, source in sorted(lock["sources"].items()):
        filename = Path(urllib.parse.urlparse(source["url"]).path).name
        # Source names and hashes are tracked inputs, never untrusted guest data.
        cached = provided.get(name, args.cache / (name + "-" + source["sha256"] + "-" + filename))
        if name in provided and not cached.is_file():
            raise ValueError(f"missing supplied archive: {name}: {cached}")
        if not cached.exists():
            temporary = cached.with_suffix(cached.suffix + ".partial")
            try:
                with urllib.request.urlopen(source["url"], timeout=120) as incoming, temporary.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                if sha256(temporary) != source["sha256"]:
                    raise ValueError(f"source hash mismatch: {name}")
                temporary.replace(cached)
            finally:
                temporary.unlink(missing_ok=True)
        if sha256(cached) != source["sha256"]:
            raise ValueError(f"cached source hash mismatch: {name}")
        destination = args.output / name / ("sources.tar.gz" if source.get("include") else filename)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.get("include"):
            filtered_archive(cached, destination, source["include"])
        else:
            shutil.copyfile(cached, destination)
        manifest[name] = {"input_sha256": source["sha256"], "path": str(destination.relative_to(args.output)),
                          "sha256": sha256(destination), "url": source["url"]}
    if args.recipe:
        recipe = args.recipe.resolve(strict=True)
        with (args.output / "recipe.tar.gz").open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as archive:
                for path in sorted(recipe.rglob("*")):
                    relative = path.relative_to(recipe)
                    if any(part in {".git", "out", ".work", "__pycache__"} for part in relative.parts):
                        continue
                    if path.is_file() and not path.is_symlink():
                        add_bytes(archive, str(relative), path.read_bytes(), bool(path.stat().st_mode & 0o111))
    shutil.copyfile(args.lock, args.output / "inputs.lock")
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
