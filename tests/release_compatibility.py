#!/usr/bin/env python3
"""Run every applicable released conformance suite on the current compiler."""

import argparse
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def version(value):
    match = VERSION_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid release version {value!r}")
    return tuple(int(match.group(index)) for index in range(1, 4))


def output(command):
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "command failed")
    return result.stdout.strip()


def validate_deprecations(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    if set(document) != {"schema", "deprecations"} or document["schema"] != 1:
        raise ValueError("unsupported deprecation-ledger schema")
    if not isinstance(document["deprecations"], list):
        raise TypeError("deprecations must be an array")
    names = set()
    for item in document["deprecations"]:
        required = {"feature", "introduced", "removal_not_before", "replacement"}
        optional = {"removed_in"}
        if not isinstance(item, dict) or set(item) - required - optional or not required <= set(item):
            raise ValueError("deprecation entry has missing or unknown fields")
        if not all(isinstance(item[key], str) and item[key] for key in required):
            raise ValueError("deprecation fields must be non-empty strings")
        if "removed_in" in item and (
                not isinstance(item["removed_in"], str) or not item["removed_in"]):
            raise ValueError("removed_in must be a non-empty version string")
        if item["feature"] in names:
            raise ValueError(f"duplicate deprecation {item['feature']!r}")
        names.add(item["feature"])
        introduced = version(item["introduced"])
        earliest = version(item["removal_not_before"])
        if earliest <= introduced or (
                earliest[0] == introduced[0]
                and earliest[1] <= introduced[1]):
            raise ValueError(
                f"{item['feature']!r} is not deprecated for a full minor "
                "language-version transition")
        if "removed_in" in item and version(item["removed_in"]) < earliest:
            raise ValueError(
                f"{item['feature']!r} was removed before its announced window")


def release_tags(current_language):
    tags = output(["git", "tag", "--list", "v[0-9]*", "--sort=version:refname"])
    applicable = []
    for tag in tags.splitlines():
        try:
            version(tag)
            raw = output(["git", "show", f"{tag}:conformance/manifest.json"])
            manifest = json.loads(raw)
        except (ValueError, RuntimeError, json.JSONDecodeError):
            continue
        if manifest.get("language_version") == current_language:
            applicable.append(tag)
    if len(applicable) < 2:
        raise RuntimeError(
            "compatibility proof requires at least two released generations")
    return applicable


def extract_conformance(tag, destination):
    archive = subprocess.run(
        ["git", "archive", "--format=tar", tag, "conformance"],
        cwd=ROOT, capture_output=True, check=False)
    if archive.returncode:
        raise RuntimeError(archive.stderr.decode(errors="replace").strip())
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as bundle:
        for member in bundle.getmembers():
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts
                    or not (member.isfile() or member.isdir())):
                raise RuntimeError(f"unsafe archive member {member.name!r}")
        bundle.extractall(destination)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--deprecations",
        type=Path,
        default=ROOT / "compatibility" / "deprecations.json",
    )
    arguments = parser.parse_args(argv)
    validate_deprecations(arguments.deprecations)
    compiler = ROOT / "mortc.py"
    current_language = output([sys.executable, str(compiler), "--language-version"])
    version(current_language)
    tags = release_tags(current_language)
    compiler_command = (
        subprocess.list2cmdline([sys.executable, str(compiler)])
        if os.name == "nt"
        else shlex.join([sys.executable, str(compiler)])
    )
    with tempfile.TemporaryDirectory(prefix="mort-release-compat-") as temporary:
        temporary_root = Path(temporary)
        for tag in tags:
            release_root = temporary_root / tag
            release_root.mkdir()
            extract_conformance(tag, release_root)
            result = subprocess.run(
                [
                    sys.executable,
                    str(release_root / "conformance" / "run.py"),
                    "--mortc",
                    compiler_command,
                ],
                cwd=ROOT,
                text=True,
                check=False,
            )
            if result.returncode:
                return result.returncode
            print(f"PASS {tag} source compatibility")
    print(
        f"verified {len(tags)} released generations for Mort "
        f"{current_language}: {', '.join(tags)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
