#!/usr/bin/env python3
"""Build Mort release artifacts with canonical, reproducible metadata."""

import argparse
import copy
import gzip
import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(command, *, environment=None):
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"release build command failed with exit code {completed.returncode}"
        )


def _revision_epoch(revision):
    completed = subprocess.run(
        ["git", "show", "-s", "--format=%ct", revision],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"cannot resolve commit timestamp for {revision!r}")
    try:
        return int(completed.stdout.strip())
    except ValueError as error:
        raise RuntimeError("Git returned an invalid commit timestamp") from error


def _canonicalize_sdist(path, epoch):
    """Rewrite setuptools' wall-clock tar metadata into a canonical archive."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with (
            tarfile.open(path, "r:gz") as source,
            temporary.open("wb") as raw_output,
            gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw_output,
                compresslevel=9,
                mtime=epoch,
            ) as compressed,
            tarfile.open(
                fileobj=compressed,
                mode="w",
                format=tarfile.PAX_FORMAT,
            ) as output,
        ):
            for original in source.getmembers():
                member = copy.copy(original)
                member.mtime = epoch
                member.uid = 0
                member.gid = 0
                member.uname = ""
                member.gname = ""
                member.pax_headers = {}
                payload = source.extractfile(original) if original.isfile() else None
                if payload is None:
                    output.addfile(member)
                else:
                    with payload:
                        output.addfile(member, payload)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(outdir, epoch):
    outdir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update({
        "PYTHONHASHSEED": "0",
        "SOURCE_DATE_EPOCH": str(epoch),
        "TZ": "UTC",
    })
    _run(
        [sys.executable, "-m", "build", "--outdir", str(outdir)],
        environment=environment,
    )
    source_archives = sorted(outdir.glob("*.tar.gz"))
    wheels = sorted(outdir.glob("*.whl"))
    if len(source_archives) != 1 or len(wheels) != 1:
        raise RuntimeError("release build must produce exactly one sdist and one wheel")
    _canonicalize_sdist(source_archives[0], epoch)
    return [wheels[0], source_archives[0]]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", default="dist", help="artifact output directory")
    parser.add_argument("--revision", default="HEAD", help="Git revision for epoch")
    parser.add_argument(
        "--epoch",
        type=int,
        help="explicit source epoch for an exported tree without Git metadata",
    )
    args = parser.parse_args(argv)
    try:
        epoch = args.epoch if args.epoch is not None else _revision_epoch(args.revision)
        if epoch < 315532800:
            raise RuntimeError("source epoch predates the ZIP timestamp range")
        artifacts = build((ROOT / args.outdir).resolve(), epoch)
    except (OSError, RuntimeError) as error:
        print(f"release build failed: {error}", file=sys.stderr)
        return 1
    print(f"canonical release epoch: {epoch}")
    for artifact in artifacts:
        print(f"{_sha256(artifact)}  {artifact.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
