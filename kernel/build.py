#!/usr/bin/env python3
"""Build (and optionally boot) the MORT OS kernel.

    python kernel/build.py build     # compile + link -> kernel/build/kernel.elf
    python kernel/build.py check     # build, then verify it's a valid multiboot kernel
    python kernel/build.py run       # build, then boot it in QEMU

The kernel is written in Mort (kmain.mx). This script compiles it to freestanding
C with the Mort compiler, cross-compiles that plus the boot stub to 32-bit x86
with the Zig backend, and links them with linker.ld into a multiboot ELF.
"""
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import mortc  # noqa: E402

TARGET = "x86-freestanding-none"           # 32-bit x86, bare metal
BUILD = os.path.join(HERE, "build")
ELF = os.path.join(BUILD, "kernel.elf")


def _zig():
    # The kernel cross-compiles to 32-bit x86, so it needs Zig specifically —
    # not whatever host cc find_c_compiler() would prefer.
    cc = mortc.find_zig()
    if not cc:
        sys.exit("kernel build needs the Zig backend — run: pip install ziglang")
    return cc


def build():
    os.makedirs(BUILD, exist_ok=True)
    cc = _zig()

    # 1. Mort kernel -> freestanding C.
    with open(os.path.join(HERE, "kmain.mx"), encoding="utf-8") as fh:
        c_source = mortc.compile_to_c(fh.read(), freestanding=True)
    kmain_c = os.path.join(BUILD, "kmain.c")
    with open(kmain_c, "w", encoding="utf-8") as fh:
        fh.write(c_source)

    c_flags = ["-target", TARGET, "-ffreestanding", "-fno-stack-protector",
               "-fno-pie", "-fno-asynchronous-unwind-tables", "-fno-unwind-tables",
               "-O2"]
    asm_flags = ["-target", TARGET, "-fno-pie"]  # assembling needs no C flags
    kmain_o = os.path.join(BUILD, "kmain.o")
    boot_o = os.path.join(BUILD, "boot.o")
    idt_o = os.path.join(BUILD, "idt.o")

    # 2. Compile the kernel C and assemble the boot + interrupt stubs.
    subprocess.run([*cc, *c_flags, "-c", kmain_c, "-o", kmain_o], check=True)
    subprocess.run([*cc, *asm_flags, "-c", os.path.join(HERE, "boot.s"), "-o", boot_o],
                   check=True)
    subprocess.run([*cc, *asm_flags, "-c", os.path.join(HERE, "idt.s"), "-o", idt_o],
                   check=True)

    # 3. Link into a static, non-PIE multiboot ELF using our linker script.
    subprocess.run([
        *cc, "-target", TARGET, "-nostdlib", "-static", "-no-pie",
        "-Wl,-T," + os.path.join(HERE, "linker.ld"),
        "-Wl,--build-id=none",
        "-o", ELF, boot_o, kmain_o, idt_o,
    ], check=True)
    print(f"built {os.path.relpath(ELF, ROOT)}")


def check():
    build()
    with open(ELF, "rb") as fh:
        data = fh.read()

    # Explicit raises (not assert) so `python -O` can't silently skip validation.
    def require(cond, msg):
        if not cond:
            sys.exit(f"kernel check FAILED: {msg}")

    require(data[:4] == b"\x7fELF", "output is not an ELF file")
    require(data[4] == 1, "expected a 32-bit (ELFCLASS32) kernel")
    machine = struct.unpack("<H", data[18:20])[0]
    require(machine == 0x03, f"expected EM_386 (0x03), got {hex(machine)}")

    # Validate the whole multiboot header (magic, flags, checksum), 4-byte
    # aligned within the first 8 KiB — not just the magic word.
    MAGIC = 0x1BADB002
    head = data[:8192]
    offset = -1
    for i in range(0, len(head) - 11, 4):
        if struct.unpack("<I", head[i:i + 4])[0] == MAGIC:
            offset = i
            break
    require(offset >= 0, "multiboot magic not found in the first 8 KiB")
    magic, flags, checksum = struct.unpack("<III", head[offset:offset + 12])
    require(flags == 0x3, f"unexpected multiboot flags {hex(flags)} (want ALIGN|MEMINFO = 0x3)")
    require((magic + flags + checksum) & 0xFFFFFFFF == 0,
            "multiboot checksum invalid (magic + flags + checksum != 0)")

    print(f"OK: 32-bit x86 multiboot ELF; valid header at file offset {offset}")
    print("Boot it with:  python kernel/build.py run")


def _find_qemu():
    found = shutil.which("qemu-system-i386")
    if found:
        return found
    # Windows: QEMU installs here but usually isn't added to PATH.
    import glob
    for pattern in (
        r"C:\Program Files\qemu\qemu-system-i386.exe",
        r"C:\Program Files*\qemu*\qemu-system-i386.exe",
    ):
        hits = glob.glob(pattern)
        if hits:
            return hits[0]
    return None


def run():
    build()
    qemu = _find_qemu()
    if not qemu:
        sys.exit("qemu-system-i386 not found — install QEMU (e.g. `winget install "
                 "SoftwareFreedomConservancy.QEMU`) to boot the kernel.")
    print("Booting MORT OS in QEMU. Maximise the window to scale it up; "
          "Ctrl+Alt+G releases the mouse; close the window to exit.")
    # zoom-to-fit scales the little 80x25 console up when you resize the window.
    # A list argv lets subprocess quote the (space-containing) ELF path for us.
    subprocess.run([qemu, "-display", "gtk,zoom-to-fit=on", "-kernel", ELF])


COMMANDS = {"build": build, "check": check, "run": run}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd not in COMMANDS:
        sys.exit(f"unknown command {cmd!r}; use one of: {', '.join(COMMANDS)}")
    COMMANDS[cmd]()
