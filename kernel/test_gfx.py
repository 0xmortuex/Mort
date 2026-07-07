#!/usr/bin/env python3
"""Graphics-mode acceptance test. Builds the bootable ISO, boots it headless in
QEMU (the Limine path provides a linear framebuffer), types a command, and
screendumps the framebuffer — then asserts the desktop and console rendered.

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

    ppm = os.path.join(tempfile.mkdtemp(prefix="mortgfx-"), "screen.ppm")
    p = subprocess.Popen([qemu, "-cdrom", iso, "-display", "none", "-monitor", "stdio"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, bufsize=0)

    def drain(s):
        while s.read(1):
            pass

    for s in (p.stdout, p.stderr):
        threading.Thread(target=drain, args=(s,), daemon=True).start()

    try:
        time.sleep(6)  # Limine + kernel boot
        for ch in "help":  # render some console text
            p.stdin.write(f"sendkey {ch}\n".encode())
            p.stdin.flush()
            time.sleep(0.05)
        p.stdin.write(b"sendkey ret\n")
        p.stdin.flush()
        time.sleep(1)
        p.stdin.write(f"screendump {ppm.replace(chr(92), '/')}\n".encode())
        p.stdin.flush()
        time.sleep(2)
        p.stdin.write(b"quit\n")
        p.stdin.flush()
        time.sleep(1)
    finally:
        p.kill()

    check("screendump produced", os.path.exists(ppm))
    if not os.path.exists(ppm):
        print(f"\n{passed} passed, {failed} failed")
        return 1

    w, h, px = parse_ppm(ppm)

    def rgb(x, y):
        o = (y * w + x) * 3
        return px[o], px[o + 1], px[o + 2]

    check("framebuffer is 1024x768", (w, h) == (1024, 768))
    # top bar drawn (0x0d141c = 13,20,28)
    check("desktop top bar rendered", rgb(300, 3) == (13, 20, 28))
    # window title bar drawn (0x2c5364 = 44,83,100)
    check("terminal window title bar rendered", rgb(500, 172) == (44, 83, 100))
    # console interior has non-background pixels => text was rendered.
    # console text area is x 192..832, y 196..596; background is 0x0b0f14.
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

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
