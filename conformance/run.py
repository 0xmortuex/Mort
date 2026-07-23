#!/usr/bin/env python3
"""Black-box runner for the Mort language conformance manifest."""

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent


def _command(value):
    parts = shlex.split(value, posix=os.name != "nt")
    if len(parts) == 1 and parts[0].lower().endswith(".py"):
        return [sys.executable, parts[0]]
    return parts


def _run(command, *, cwd=None):
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def run_case(base_command, case, temporary):
    sources = [str(ROOT / path) for path in case["sources"]]
    extra = list(case.get("args", []))
    mode = case["mode"]

    if mode == "check":
        result = _run([*base_command, *sources, "--check", *extra])
        if result.returncode != 0:
            return False, f"check failed:\n{result.stderr}"
        return True, ""

    if mode == "reject":
        result = _run([*base_command, *sources, "--check", *extra])
        expected = case["stderr_contains"]
        if result.returncode == 0:
            return False, "invalid program was accepted"
        if expected not in result.stderr:
            return False, (
                f"diagnostic did not contain {expected!r}:\n{result.stderr}")
        return True, ""

    if mode not in ("run", "run-fail"):
        return False, f"unknown mode {mode!r}"

    suffix = ".exe" if os.name == "nt" else ""
    output = Path(temporary) / (case["id"].replace(".", "_") + suffix)
    build = _run([*base_command, *sources, "-o", str(output), *extra])
    if build.returncode != 0:
        return False, f"build failed:\n{build.stderr}\n{build.stdout}"
    result = _run([str(output)])
    if mode == "run-fail":
        if result.returncode == 0:
            return False, "program was expected to fail but exited successfully"
        expected = case["stderr_contains"]
        if expected not in result.stderr:
            return False, (
                f"runtime diagnostic did not contain {expected!r}:\n"
                f"{result.stderr}")
        return True, ""
    expected_exit = case.get("exit", 0)
    if result.returncode != expected_exit:
        return False, (
            f"exit was {result.returncode}, expected {expected_exit}; "
            f"stderr:\n{result.stderr}")
    if result.stdout != case.get("stdout", ""):
        return False, (
            f"stdout was {result.stdout!r}, "
            f"expected {case.get('stdout', '')!r}")
    if result.stderr != case.get("stderr", ""):
        return False, (
            f"stderr was {result.stderr!r}, "
            f"expected {case.get('stderr', '')!r}")
    return True, ""


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the Mort executable conformance suite.")
    parser.add_argument(
        "--mortc",
        default=str(ROOT.parent / "mortc.py"),
        help="compiler executable, Python script, or quoted command",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="run only an exact case id (repeatable)",
    )
    args = parser.parse_args(argv)

    manifest = json.loads(
        (ROOT / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1:
        parser.error("unsupported conformance manifest schema")

    selected = [
        case for case in manifest["cases"]
        if not args.case or case["id"] in args.case
    ]
    unknown = set(args.case) - {case["id"] for case in selected}
    if unknown:
        parser.error("unknown case(s): " + ", ".join(sorted(unknown)))

    base_command = _command(args.mortc)
    failures = []
    version = _run([*base_command, "--language-version"])
    expected_version = manifest["language_version"]
    if version.returncode != 0 or version.stdout.strip() != expected_version:
        print(
            "compiler language version mismatch: "
            f"expected {expected_version!r}, got {version.stdout.strip()!r}",
            file=sys.stderr,
        )
        if version.stderr:
            print(version.stderr, file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="mort-conformance-") as temporary:
        for case in selected:
            passed, detail = run_case(base_command, case, temporary)
            state = "PASS" if passed else "FAIL"
            print(f"{state} {case['id']}")
            if not passed:
                failures.append((case["id"], detail))

    if failures:
        print(file=sys.stderr)
        for case_id, detail in failures:
            print(f"{case_id}: {detail}", file=sys.stderr)
        print(
            f"\n{len(failures)} of {len(selected)} conformance case(s) failed",
            file=sys.stderr,
        )
        return 1

    print(
        f"\n{len(selected)} conformance case(s) passed for Mort "
        f"{manifest['language_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
