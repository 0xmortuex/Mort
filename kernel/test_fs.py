#!/usr/bin/env python3
"""MortFS acceptance test — boots the real kernel in QEMU and exercises the
disk stack end to end: ls/cat/write/rm/run, error paths, and persistence
across a reboot.

    python kernel/test_fs.py

Needs QEMU (same discovery as build.py). Scratch disk images go to the system
temp dir, never the repo. Exit code 0 = all passed.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import test as T  # noqa: E402  (the QEMU harness)

TMP = tempfile.mkdtemp(prefix="mortfs-test-")
IMG = os.path.join(TMP, "fs.img")
BADIMG = os.path.join(TMP, "bad.img")

passed = 0
failed = 0


def check(label, ok, h=None):
    global passed, failed
    if ok:
        passed += 1
        print(f"PASS: {label}")
    else:
        failed += 1
        print(f"FAIL: {label}")
        if h is not None:
            print(T.screen_text(h))


def expect(h, text, label, timeout=15):
    check(label, T.wait_for(h, text, timeout_s=timeout), h)


def main():
    # fixtures: a seeded image (43-byte, two-line file) and a zeroed one
    seed = os.path.join(TMP, "seed.txt")
    with open(seed, "w", newline="\n") as fh:
        fh.write("first line from mkfs\nsecond line from mkfs\n")
    subprocess.run([sys.executable, os.path.join(HERE, "mkfs.py"), IMG,
                    "--add", seed + ":seeded.txt"], check=True)
    with open(BADIMG, "wb") as fh:
        fh.write(b"\x00" * (16 * 1024 * 1024))

    # -- session 1: commands and error paths
    h = T.boot(disk_img=IMG)
    try:
        expect(h, "MORT OS", "boot banner")
        T.type_line(h, "ls")
        expect(h, "seeded.txt", "ls shows seeded file")
        expect(h, "43 bytes", "ls shows correct size")
        T.type_line(h, "cat seeded.txt")
        expect(h, "second line from mkfs", "cat prints seeded content")

        T.type_line(h, "write notes.txt remember to feed the kernel")
        T.type_line(h, "ls")
        expect(h, "notes.txt", "ls shows written file")
        T.type_line(h, "write notes.txt second entry here")
        T.type_line(h, "cat notes.txt")
        expect(h, "remember to feed the kernel", "cat line 1 after append")
        expect(h, "second entry here", "cat line 2 after append")

        T.type_line(h, "cat nope.txt")
        expect(h, "not found: nope.txt", "cat missing file errors")
        T.type_line(h, "write onlyname")
        expect(h, "usage: write <name> <text>", "write without text shows usage")

        T.type_line(h, "rm seeded.txt")
        T.type_line(h, "cat seeded.txt")
        expect(h, "not found: seeded.txt", "rm removes the file")

        # author a script entirely in-OS, then run it
        T.type_line(h, "write job.txt echo script-says-hi")
        T.type_line(h, "write job.txt uptime")
        T.type_line(h, "run job.txt")
        expect(h, "script-says-hi", "run executes line 1")
        expect(h, "uptime:", "run executes line 2")

        # nested run is rejected; the outer script continues
        T.type_line(h, "write meta.txt run job.txt")
        T.type_line(h, "write meta.txt echo after-nested")
        T.type_line(h, "run meta.txt")
        expect(h, "nested run not allowed", "nested run rejected")
        expect(h, "after-nested", "script continues after rejection")
    finally:
        T.shutdown(h)

    # -- session 2: persistence across reboot
    h = T.boot(disk_img=IMG)
    try:
        expect(h, "MORT OS", "reboot banner")
        T.type_line(h, "cat notes.txt")
        expect(h, "remember to feed the kernel", "PERSISTENCE: file survives reboot")
        T.type_line(h, "ls")
        expect(h, "job.txt", "PERSISTENCE: ls intact after reboot")
    finally:
        T.shutdown(h)

    # -- session 3: disk present but not a MortFS
    h = T.boot(disk_img=BADIMG)
    try:
        expect(h, "MORT OS", "bad-magic banner")
        T.type_line(h, "ls")
        expect(h, "bad filesystem", "zeroed image reports bad filesystem")
    finally:
        T.shutdown(h)

    # -- session 4: no disk at all
    h = T.boot()
    try:
        expect(h, "MORT OS", "diskless banner")
        T.type_line(h, "ls")
        expect(h, "no disk", "diskless ls says no disk")
        T.type_line(h, "mods")
        expect(h, "modules:", "mods still lists boot modules")
    finally:
        T.shutdown(h)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
