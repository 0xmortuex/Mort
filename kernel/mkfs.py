#!/usr/bin/env python3
"""Create a MortFS v1 disk image for MORT OS (host-side mkfs).

    python kernel/mkfs.py disk.img                          # empty 16 MiB MortFS
    python kernel/mkfs.py disk.img --size 32                # size in MiB (default 16)
    python kernel/mkfs.py disk.img --add notes.txt          # seed a host file
    python kernel/mkfs.py disk.img --add host.txt:name.txt  # seed under another name

WARNING: the image at the given path is created OR OVERWRITTEN — running this
against an existing image wipes everything on it. `python kernel/build.py disk`
is the non-destructive path (it creates the image only if missing).

On-disk format (kernel/docs/fs-design.md, section 2): sector 0 is the
superblock, sectors 1..8 the 64-entry file table, and data extents start at
sector 16, a fixed 128 sectors (64 KiB) per file. All integers are
little-endian u32, matching what the kernel's raw *u32 loads produce on x86.
"""
import argparse
import os
import struct
import sys

SECTOR = 512
MAGIC = 0x3153464D        # the bytes b"MFS1" read as a little-endian u32
VERSION = 1
MAX_FILES = 64
MAX_NAME = 23             # + terminating NUL fills the 24-byte name field
MAX_SIZE = 65536          # one 128-sector extent; also the kernel's FILEBUF size
EXTENT_SECTORS = 128
DATA_START = 16           # first data sector; next_free_sector starts here
TABLE_OFFSET = 512        # file table begins at sector 1
ENTRY_SIZE = 64


def _parse_add(spec):
    """Split an --add spec into (host, name).

    NAME defaults to basename(HOST). A colon at index 1 is a Windows drive
    letter (C:\\...), not a separator, so only a later colon splits.
    """
    idx = spec.rfind(":")
    if idx > 1:
        host, name = spec[:idx], spec[idx + 1:]
    else:
        host, name = spec, ""
    if not name:
        name = os.path.basename(host)
    return host, name


def make(path, size_mib=16, adds=()):
    """Create (or overwrite) a MortFS v1 image at `path`.

    `adds` is an iterable of "HOST[:NAME]" specs (or (host, name) tuples);
    each seeds one file. Any validation failure exits with a message —
    nothing is ever truncated silently.
    """
    if size_mib < 1:
        sys.exit(f"mkfs: --size must be at least 1 MiB (got {size_mib})")
    total_sectors = size_mib * 1024 * 1024 // SECTOR

    # 1. Read and validate every seed file before touching the image.
    files = []  # (name, content) with content already CRLF-normalized
    seen = set()
    for spec in adds:
        host, name = spec if isinstance(spec, tuple) else _parse_add(spec)
        if not name or len(name) > MAX_NAME or not name.isascii() \
                or not name.isprintable():
            sys.exit(f"mkfs: bad file name {name!r} "
                     f"(1..{MAX_NAME} printable ASCII chars)")
        if name in seen:
            sys.exit(f"mkfs: duplicate file name {name!r}")
        seen.add(name)
        try:
            with open(host, "rb") as fh:
                content = fh.read()
        except OSError as e:
            sys.exit(f"mkfs: cannot read {host}: {e}")
        # The kernel's `run`/`cat` want plain LF lines; normalize host CRLFs.
        content = content.replace(b"\r\n", b"\n")
        if len(content) > MAX_SIZE:
            sys.exit(f"mkfs: {host} is {len(content)} bytes after CRLF->LF "
                     f"normalization; the max file size is {MAX_SIZE}")
        files.append((name, content))
    if len(files) > MAX_FILES:
        sys.exit(f"mkfs: {len(files)} files added; the table holds {MAX_FILES}")
    if DATA_START + EXTENT_SECTORS * len(files) > total_sectors:
        sys.exit(f"mkfs: {len(files)} files need "
                 f"{DATA_START + EXTENT_SECTORS * len(files)} sectors but a "
                 f"{size_mib} MiB image has only {total_sectors} — grow --size")

    # 2. Assemble the whole image in RAM (16 MiB default — trivial), zero-filled.
    image = bytearray(total_sectors * SECTOR)
    next_free = DATA_START
    for i, (name, content) in enumerate(files):
        entry = TABLE_OFFSET + i * ENTRY_SIZE
        image[entry:entry + 24] = name.encode("ascii").ljust(24, b"\x00")
        image[entry + 24:entry + 40] = struct.pack(
            "<IIII", 1, len(content), next_free, EXTENT_SECTORS)
        image[next_free * SECTOR:next_free * SECTOR + len(content)] = content
        next_free += EXTENT_SECTORS
    image[0:20] = struct.pack("<IIIII", MAGIC, VERSION, len(files), next_free,
                              total_sectors)
    with open(path, "wb") as fh:
        fh.write(image)

    # 3. Round-trip self-check: re-parse the written image and compare.
    _self_check(path, total_sectors, files)

    print(f"wrote {path}: {size_mib} MiB MortFS v1, {len(files)} file(s)")
    for i, (name, content) in enumerate(files):
        print(f"  {name:<24}{len(content):>7} bytes @ sector "
              f"{DATA_START + i * EXTENT_SECTORS}")


def _self_check(path, total_sectors, files):
    """Re-open the image, re-parse superblock + table + content, and compare."""
    def require(cond, msg):
        if not cond:
            sys.exit(f"mkfs self-check FAILED: {msg}")

    with open(path, "rb") as fh:
        data = fh.read()
    require(len(data) == total_sectors * SECTOR,
            f"image is {len(data)} bytes, expected {total_sectors * SECTOR}")
    magic, version, count, next_free, total = struct.unpack_from("<IIIII", data, 0)
    require(magic == MAGIC, f"magic {magic:#010x} != {MAGIC:#010x}")
    require(version == VERSION, f"version {version} != {VERSION}")
    require(count == len(files), f"file_count {count} != {len(files)}")
    require(next_free == DATA_START + EXTENT_SECTORS * len(files),
            f"next_free_sector {next_free} != "
            f"{DATA_START + EXTENT_SECTORS * len(files)}")
    require(total == total_sectors, f"total_sectors {total} != {total_sectors}")
    for i, (name, content) in enumerate(files):
        entry = TABLE_OFFSET + i * ENTRY_SIZE
        want_name = name.encode("ascii").ljust(24, b"\x00")
        require(data[entry:entry + 24] == want_name, f"entry {i} name mismatch")
        used, size, start, cap = struct.unpack_from("<IIII", data, entry + 24)
        want = (1, len(content), DATA_START + i * EXTENT_SECTORS, EXTENT_SECTORS)
        require((used, size, start, cap) == want,
                f"entry {i} fields {(used, size, start, cap)} != {want}")
        require(data[start * SECTOR:start * SECTOR + size] == content,
                f"entry {i} content mismatch")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Create a MortFS v1 disk image for MORT OS. WARNING: the "
                    "image is created OR OVERWRITTEN — everything on an "
                    "existing image is lost. Use `python kernel/build.py disk` "
                    "to create it only if missing.")
    ap.add_argument("image", help="path of the disk image to create/overwrite")
    ap.add_argument("--size", type=int, default=16, metavar="N",
                    help="image size in MiB (default 16)")
    ap.add_argument("--add", action="append", default=[], metavar="HOST[:NAME]",
                    help="seed host file HOST into the image as NAME (default: "
                         "basename of HOST); repeatable, max 64 files")
    args = ap.parse_args(argv)
    make(args.image, size_mib=args.size, adds=args.add)


if __name__ == "__main__":
    main()
