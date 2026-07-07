#!/usr/bin/env python3
"""Program-execution acceptance test. Builds the sample Mort programs, seeds
them onto a fresh MortFS image, boots the kernel in QEMU, and `exec`s them —
proving a Mort program compiled to a flat binary loads off disk, runs at
0x00A00000, makes int 0x80 syscalls, and returns to the shell.

    python kernel/test_exec.py

Needs QEMU + `pip install ziglang`. Scratch image goes to the temp dir.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import build  # noqa: E402  (compile programs + reuse the mkfs seeder)
import mkfs  # noqa: E402
import test as T  # noqa: E402  (QEMU harness)

TMP = tempfile.mkdtemp(prefix="mortexec-test-")
IMG = os.path.join(TMP, "exec.img")

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


def main():
    # build every sample program to a flat binary, then seed them raw
    bins = []
    for mx in build._program_sources():
        out = os.path.join(build.BUILD, os.path.splitext(os.path.basename(mx))[0] + ".bin")
        build.build_program(mx, out)
        bins.append((out, os.path.basename(out)))
    mkfs.make(IMG, bins=bins)
    print(f"seeded {len(bins)} program(s) into {IMG}")

    h = T.boot(disk_img=IMG)
    try:
        check("boot banner", T.wait_for(h, "MORT OS"), h)
        T.type_line(h, "ls")
        check("ls shows hello.bin", T.wait_for(h, "hello.bin", timeout_s=15), h)
        check("ls shows count.bin", T.wait_for(h, "count.bin", timeout_s=15), h)

        T.type_line(h, "exec hello.bin")
        check("exec hello: syscall output",
              T.wait_for(h, "hello from a real mort program!", timeout_s=15), h)
        T.type_line(h, "echo back-in-shell")
        check("shell alive after program returns",
              T.wait_for(h, "back-in-shell", timeout_s=15), h)

        T.type_line(h, "exec count.bin")
        check("exec count: prints 'one'", T.wait_for(h, "one", timeout_s=15), h)
        check("exec count: prints 'three'", T.wait_for(h, "three", timeout_s=15), h)

        # interactive program: it prompts, reads a line via syscall, greets
        T.type_line(h, "exec ask.bin")
        check("interactive program prompts",
              T.wait_for(h, "what is your name?", timeout_s=15), h)
        T.type_line(h, "fadi")
        check("interactive program reads input + greets",
              T.wait_for(h, "hello, fadi", timeout_s=15), h)

        T.type_line(h, "exec nope.bin")
        check("exec missing program errors",
              T.wait_for(h, "not found: nope.bin", timeout_s=15), h)
        # the shell must survive an interactive program cleanly
        T.type_line(h, "echo shell-still-ok")
        check("shell alive after interactive program",
              T.wait_for(h, "shell-still-ok", timeout_s=15), h)
    finally:
        T.shutdown(h)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
