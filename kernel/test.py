#!/usr/bin/env python3
"""Automated test harness for MORT OS.

Boots the kernel headless in QEMU, types commands over the QEMU human
monitor (`sendkey`), reads the VGA text screen back out of guest memory
(`memsave 0xb8000`), and asserts on what the shell drew.

    python kernel/test.py smoke                 # build + boot + run the smoke tests
    python kernel/test.py smoke --disk foo.img  # same, with a disk attached as -hda

Python stdlib only. Works on Windows (pipes to `-monitor stdio` are drained
by a background thread; nothing here assumes a tty or select()).

API for other tests:

    handle = boot(disk_img=None)         # launch QEMU, wait for the monitor
    type_line(handle, "echo hi")         # sendkey each char, then Enter
    text = screen_text(handle)           # the 80x25 VGA screen as 25 lines
    ok = wait_for(handle, "hi", 10)      # poll the screen for a substring
    shutdown(handle)                     # monitor `quit` + kill, always safe
"""
import argparse
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ELF = os.path.join(HERE, "build", "kernel.elf")

MONITOR_PROMPT = b"(qemu)"

# How long QEMU holds each injected key down (ms), and how long we pause after
# each key. The pause MUST exceed the hold time: `sendkey shift-a` releases
# shift only after the hold time, so a faster next key would land shifted.
KEY_HOLD_MS = 40
KEY_GAP_S = 0.07

# ----- char -> QEMU sendkey name -----

_SYMS = {
    " ": "spc", "-": "minus", "=": "equal", ".": "dot", ",": "comma",
    "/": "slash", ";": "semicolon", "'": "apostrophe", "[": "bracket_left",
    "]": "bracket_right", "\\": "backslash", "`": "grave_accent",
}
_SHIFT_SYMS = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "minus", "+": "equal", "<": "comma",
    ">": "dot", "?": "slash", ":": "semicolon", '"': "apostrophe",
    "{": "bracket_left", "}": "bracket_right", "|": "backslash",
    "~": "grave_accent",
}


def key_name(ch):
    """QEMU sendkey name for a single character (raises on unmappable)."""
    if "a" <= ch <= "z" or "0" <= ch <= "9":
        return ch
    if "A" <= ch <= "Z":
        return "shift-" + ch.lower()
    if ch in _SYMS:
        return _SYMS[ch]
    if ch in _SHIFT_SYMS:
        return "shift-" + _SHIFT_SYMS[ch]
    raise ValueError(f"no sendkey mapping for character {ch!r}")


# ----- QEMU process handle -----

class QemuHandle:
    def __init__(self, proc):
        self.proc = proc
        self.out = bytearray()          # everything QEMU wrote to stdout
        self.err = bytearray()          # everything QEMU wrote to stderr
        self.lock = threading.Lock()
        self._dump = os.path.join(
            tempfile.gettempdir(), f"mort_vga_{proc.pid}.bin")
        self._readers = [
            threading.Thread(target=self._drain, args=(proc.stdout, self.out),
                             daemon=True),
            threading.Thread(target=self._drain, args=(proc.stderr, self.err),
                             daemon=True),
        ]
        for t in self._readers:
            t.start()

    def _drain(self, pipe, buf):
        # Raw (bufsize=0) pipe: read() returns as soon as any bytes arrive.
        # A dedicated thread per pipe is the only portable way to avoid
        # blocking on Windows, where select() doesn't work on pipes.
        while True:
            try:
                chunk = pipe.read(4096)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            with self.lock:
                buf.extend(chunk)

    def alive(self):
        return self.proc.poll() is None

    def out_len(self):
        with self.lock:
            return len(self.out)

    def out_since(self, mark):
        with self.lock:
            return bytes(self.out[mark:])

    def tail(self, n=800):
        with self.lock:
            return (bytes(self.out[-n:]).decode("ascii", "replace")
                    + " | stderr: "
                    + bytes(self.err[-n:]).decode("ascii", "replace"))


def _monitor(handle, cmd, timeout_s=10):
    """Send one human-monitor command; return its output (text between the
    echoed command and the next '(qemu)' prompt)."""
    if not handle.alive():
        raise RuntimeError(f"QEMU exited (rc={handle.proc.returncode}); "
                           f"last output: {handle.tail()}")
    mark = handle.out_len()
    handle.proc.stdin.write(cmd.encode("ascii") + b"\n")
    handle.proc.stdin.flush()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        data = handle.out_since(mark)
        if MONITOR_PROMPT in data:
            text = data.split(MONITOR_PROMPT, 1)[0]
            return text.decode("ascii", "replace")
        if not handle.alive():
            raise RuntimeError(
                f"QEMU died during {cmd!r} (rc={handle.proc.returncode}); "
                f"output: {handle.tail()}")
        time.sleep(0.01)
    raise TimeoutError(
        f"no monitor prompt within {timeout_s}s after {cmd!r}; "
        f"output tail: {handle.tail()}")


def _wait_first_prompt(handle, timeout_s=15):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if MONITOR_PROMPT in handle.out_since(0):
            return
        if not handle.alive():
            raise RuntimeError(
                f"QEMU exited at startup (rc={handle.proc.returncode}); "
                f"output: {handle.tail()}")
        time.sleep(0.02)
    raise TimeoutError(f"QEMU monitor never prompted; output: {handle.tail()}")


# ----- public API -----

def boot(disk_img=None, kernel_elf=ELF):
    """Boot the kernel headless; return a handle once the monitor is ready."""
    qemu = _find_qemu()
    if not qemu:
        raise RuntimeError("qemu-system-i386 not found — install QEMU "
                           "(winget install SoftwareFreedomConservancy.QEMU)")
    if not os.path.exists(kernel_elf):
        raise RuntimeError(f"kernel not built: {kernel_elf}")
    cmd = [qemu, "-display", "none", "-monitor", "stdio",
           "-kernel", kernel_elf]
    if disk_img:
        cmd += ["-hda", disk_img]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, bufsize=0)          # raw, unbuffered pipes
    handle = QemuHandle(proc)
    try:
        _wait_first_prompt(handle)
    except Exception:
        shutdown(handle)
        raise
    return handle


def type_line(handle, text):
    """Type `text` on the guest's PS/2 keyboard, then press Enter."""
    for ch in text:
        _monitor(handle, f"sendkey {key_name(ch)} {KEY_HOLD_MS}")
        time.sleep(KEY_GAP_S)
    _monitor(handle, f"sendkey ret {KEY_HOLD_MS}")
    time.sleep(KEY_GAP_S)


def screen_text(handle):
    """The current 80x25 VGA text screen, as 25 newline-joined lines."""
    raw = _dump_vga_memsave(handle)
    if raw is None:
        raw = _dump_vga_xp(handle)
    chars = raw[0::2]                     # cells are (char, attr) byte pairs
    rows = []
    for r in range(25):
        row = chars[r * 80:(r + 1) * 80]
        rows.append("".join(chr(b) if 32 <= b < 127 else " " for b in row))
    return "\n".join(rows)


def wait_for(handle, substring, timeout_s=10):
    """Poll the screen until `substring` appears. Returns True/False."""
    deadline = time.monotonic() + timeout_s
    while True:
        if substring in screen_text(handle):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def shutdown(handle):
    """Quit QEMU via the monitor; kill it if it lingers. Never raises."""
    try:
        if handle.alive():
            try:  # `quit` never prompts again, so don't wait for one
                handle.proc.stdin.write(b"quit\n")
                handle.proc.stdin.flush()
            except OSError:
                pass
            try:
                handle.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                handle.proc.kill()
                handle.proc.wait(timeout=5)
    except Exception:
        try:
            handle.proc.kill()
        except Exception:
            pass
    finally:
        for pipe in (handle.proc.stdin, handle.proc.stdout, handle.proc.stderr):
            try:
                pipe.close()
            except Exception:
                pass
        try:
            os.remove(handle._dump)
        except OSError:
            pass


# ----- VGA dumping (memsave primary, xp fallback) -----

def _dump_vga_memsave(handle):
    """Dump 0xB8000..0xB8FA0 to a host file via `memsave`; None on failure."""
    path = handle._dump
    if " " in path:
        return None       # monitor filename args don't survive spaces
    try:
        os.remove(path)
    except OSError:
        pass
    # Forward slashes: fine for Windows, and nothing for the monitor to eat.
    out = _monitor(handle, f"memsave 0xb8000 4000 {path.replace(os.sep, '/')}")
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if len(data) != 4000:
        return None
    if "could not" in out.lower() or "error" in out.lower():
        return None
    return data


def _dump_vga_xp(handle):
    """Fallback: parse `xp /2000wx 0xb8000` (hex words) into 4000 raw bytes."""
    out = _monitor(handle, "xp /2000wx 0xb8000", timeout_s=20)
    raw = bytearray()
    for line in out.splitlines():
        if ":" not in line:
            continue
        _, _, rest = line.partition(":")
        for tok in rest.split():
            if not tok.startswith("0x"):
                continue
            word = int(tok, 16)
            raw += bytes((word & 0xFF, (word >> 8) & 0xFF,
                          (word >> 16) & 0xFF, (word >> 24) & 0xFF))
    if len(raw) < 4000:
        raise RuntimeError(
            f"xp fallback parsed only {len(raw)} bytes of VGA memory")
    return bytes(raw[:4000])


def _find_qemu():
    sys.path.insert(0, HERE)
    import build as kernel_build
    return kernel_build._find_qemu()


# ----- smoke test -----

def smoke(disk_img=None):
    # Build first (build.py locates the Mort compiler via its parent dir).
    sys.path.insert(0, HERE)
    import build as kernel_build
    kernel_build.build()

    results = []

    def check(name, ok, handle=None):
        results.append((name, ok))
        print(f"{'PASS' if ok else 'FAIL'}: {name}")
        if not ok and handle is not None:
            print("--- screen at failure ---")
            print(screen_text(handle))
            print("-------------------------")

    handle = boot(disk_img=disk_img)
    try:
        check("boot banner shows 'MORT OS'",
              wait_for(handle, "MORT OS", timeout_s=15), handle)

        type_line(handle, "help")
        check("'help' lists the commands",
              wait_for(handle, "files: ls cat write rm run exec mods",
                       timeout_s=10), handle)

        type_line(handle, "echo hi-there")
        check("'echo hi-there' echoes back",
              wait_for(handle, "hi-there", timeout_s=10), handle)

        type_line(handle, "uptime")
        check("'uptime' reports uptime",
              wait_for(handle, "uptime:", timeout_s=10), handle)
    finally:
        shutdown(handle)

    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 0 if not failed else 1


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_smoke = sub.add_parser("smoke", help="build, boot, and smoke-test the shell")
    p_smoke.add_argument("--disk", default=None,
                         help="optional disk image to attach as -hda")
    args = parser.parse_args(argv)
    if args.cmd == "smoke":
        return smoke(disk_img=args.disk)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
