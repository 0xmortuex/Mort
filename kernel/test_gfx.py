#!/usr/bin/env python3
"""Graphics-mode acceptance test. Builds the bootable ISO, boots it headless in
QEMU (the Limine path provides a linear framebuffer), and screendumps the
framebuffer to assert the desktop, the console, and app-switching all render.

    python kernel/test_gfx.py

Needs QEMU + the ISO toolchain (Limine/xorriso, downloaded+cached by build.py).
Stdlib only — parses the PPM screendump directly, no Pillow.
"""
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
import build  # noqa: E402

passed = 0
failed = 0
TMP = tempfile.mkdtemp(prefix="mortgfx-")


def check(label, ok):
    global passed, failed
    if ok:
        passed += 1
        print(f"PASS: {label}")
    else:
        failed += 1
        print(f"FAIL: {label}")


def parse_ppm(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:2] != b"P6":
        raise ValueError("not a P6 ppm")
    idx, fields = 2, []
    while len(fields) < 3:
        while data[idx] in b" \t\n\r":
            idx += 1
        start = idx
        while data[idx] not in b" \t\n\r":
            idx += 1
        fields.append(int(data[start:idx]))
    idx += 1
    w, h, _ = fields
    return w, h, data[idx:]


def main():
    build.iso()  # -> build/mort.iso (uses cached Limine/xorriso)
    iso = os.path.join(build.BUILD, "mort.iso")
    qemu = build._find_qemu()
    if not qemu:
        sys.exit("qemu-system-i386 not found")

    p = subprocess.Popen([qemu, "-cdrom", iso, "-display", "none", "-monitor", "stdio"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, bufsize=0)

    def drain(s):
        while s.read(1):
            pass

    for s in (p.stdout, p.stderr):
        threading.Thread(target=drain, args=(s,), daemon=True).start()

    def key(k):
        p.stdin.write(f"sendkey {k}\n".encode())
        p.stdin.flush()
        time.sleep(0.1)

    n = [0]

    def capture():
        n[0] += 1
        ppm = os.path.join(TMP, f"s{n[0]}.ppm")
        p.stdin.write(f"screendump {ppm.replace(chr(92), '/')}\n".encode())
        p.stdin.flush()
        time.sleep(1.5)
        return parse_ppm(ppm)

    try:
        time.sleep(6)  # Limine + kernel boot
        for ch in "help":  # render some console text
            key(ch)
        key("ret")
        time.sleep(0.5)
        w, h, px = capture()

        def rgb(x, y, buf=None):
            b = buf if buf is not None else px
            o = (y * w + x) * 3
            return b[o], b[o + 1], b[o + 2]

        ACTIVE = (44, 83, 100)   # highlighted tab bg (0x2c5364)
        INACTIVE = (13, 20, 28)  # top bar bg (0x0d141c)

        check("framebuffer is 1024x768", (w, h) == (1024, 768))
        check("desktop top bar rendered", rgb(150, 3) == INACTIVE)
        check("terminal window title bar rendered", rgb(500, 172) == (44, 83, 100))
        # F1 (terminal) tab highlighted at boot (padding pixel left of the label)
        check("terminal tab active at boot", rgb(296, 10) == ACTIVE)
        # console has non-background pixels => glyphs rendered
        bg = (11, 15, 20)
        text_pixels = 0
        y = 196
        while y < 340 and text_pixels < 50:
            x = 192
            while x < 640:
                if rgb(x, y) != bg:
                    text_pixels += 1
                x += 1
            y += 1
        check("console shows rendered glyph pixels", text_pixels >= 50)

        # switch to Files (F2)
        key("f2")
        time.sleep(0.4)
        _, _, px2 = capture()
        check("files tab active after F2", rgb(466, 10, px2) == ACTIVE)
        check("terminal tab inactive after F2", rgb(296, 10, px2) == INACTIVE)

        # switch to Vex (F3)
        key("f3")
        time.sleep(0.4)
        _, _, px3 = capture()
        check("vex tab active after F3", rgb(596, 10, px3) == ACTIVE)

        # back to terminal (F1)
        key("f1")
        time.sleep(0.4)
        _, _, px4 = capture()
        check("terminal tab active after F1", rgb(296, 10, px4) == ACTIVE)
    finally:
        try:
            p.stdin.write(b"quit\n")
            p.stdin.flush()
            time.sleep(0.5)
        except Exception:
            pass
        p.kill()

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
