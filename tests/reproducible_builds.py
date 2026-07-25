#!/usr/bin/env python3
"""Prove that a Mort revision produces byte-identical release artifacts."""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(command, *, cwd, environment=None, timeout=300):
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        rendered = subprocess.list2cmdline([str(item) for item in command])
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: {rendered}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed.stdout.strip()


def _extract_git_archive(archive_path, destination):
    """Extract the trusted Git archive without path traversal or links."""
    root = destination.resolve()
    with tarfile.open(archive_path, "r:") as archive:
        for member in archive.getmembers():
            target = (root / member.name).resolve()
            try:
                target.relative_to(root)
            except ValueError as error:
                raise RuntimeError(
                    f"Git archive contains unsafe path {member.name!r}"
                ) from error
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise RuntimeError(
                    f"Git archive contains unsupported entry {member.name!r}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"cannot read archived file {member.name!r}")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            os.chmod(target, member.mode & 0o777)
            os.utime(target, (member.mtime, member.mtime))


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _build_once(source_archive, build_root, epoch):
    source = build_root / "source"
    distribution = build_root / "dist"
    source.mkdir(parents=True)
    distribution.mkdir()
    _extract_git_archive(source_archive, source)
    _run(
        [
            sys.executable,
            str(source / "tools" / "build_release.py"),
            "--outdir",
            str(distribution),
            "--epoch",
            str(epoch),
        ],
        cwd=source,
    )
    artifacts = sorted(
        path for path in distribution.iterdir()
        if path.suffix == ".whl" or path.name.endswith(".tar.gz")
    )
    if len(artifacts) != 2:
        names = ", ".join(path.name for path in artifacts) or "none"
        raise RuntimeError(f"expected wheel and sdist, found: {names}")
    return {path.name: _sha256(path) for path in artifacts}


def verify(revision):
    epoch_text = _run(
        ["git", "show", "-s", "--format=%ct", revision],
        cwd=ROOT,
    )
    try:
        epoch = int(epoch_text)
    except ValueError as error:
        raise RuntimeError(f"invalid commit timestamp {epoch_text!r}") from error

    with tempfile.TemporaryDirectory(prefix="mort-reproducible-") as directory:
        temporary = Path(directory)
        source_archive = temporary / "source.tar"
        _run(
            [
                "git",
                "archive",
                "--format=tar",
                f"--output={source_archive}",
                revision,
            ],
            cwd=ROOT,
        )
        first = _build_once(source_archive, temporary / "first", epoch)
        second = _build_once(source_archive, temporary / "second", epoch)

    if first != second:
        lines = ["release artifacts are not reproducible:"]
        for name in sorted(set(first) | set(second)):
            lines.append(
                f"  {name}: first={first.get(name, 'missing')} "
                f"second={second.get(name, 'missing')}"
            )
        raise RuntimeError("\n".join(lines))
    return epoch, first


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--revision",
        default="HEAD",
        help="Git revision to build twice (default: HEAD)",
    )
    args = parser.parse_args(argv)
    try:
        epoch, artifacts = verify(args.revision)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"reproducible-build test failed: {error}", file=sys.stderr)
        return 1
    print(f"reproducible-build epoch: {epoch}")
    for name, digest in sorted(artifacts.items()):
        print(f"{digest}  {name}")
    print("reproducible-build test passed: two clean source trees are identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
