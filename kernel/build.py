#!/usr/bin/env python3
"""Build (and optionally boot) the MORT OS kernel.

    python kernel/build.py build     # compile + link -> kernel/build/kernel.elf
    python kernel/build.py check     # build, then verify it's a valid multiboot kernel
    python kernel/build.py run       # build, then boot it fullscreen in QEMU
    python kernel/build.py window    # same, but in a normal window
    python kernel/build.py iso       # build a bootable mort.iso (Limine + xorriso)
    python kernel/build.py run-iso   # build the ISO, then boot it in QEMU (-cdrom)
    python kernel/build.py disk      # create build/disk.img (MortFS) iff missing
    python kernel/build.py prog      # compile every programs/*.mx -> build/<name>.bin

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
DISK = os.path.join(BUILD, "disk.img")

# User programs: compiled from programs/*.mx into flat binaries the kernel
# loads at 0x00A00000 and enters at byte 0 (see programs/prog.ld / pstart.s).
PROGRAMS = os.path.join(HERE, "programs")

# Bootable-ISO tooling (downloaded + cached once under kernel/tools/).
TOOLS = os.path.join(HERE, "tools")
ISO = os.path.join(BUILD, "mort.iso")
# Limine boots our multiboot1 kernel unchanged; both tools are portable Windows
# .exe files, so no WSL/MSYS2 is needed. Pinned to a stable Limine branch.
LIMINE_ZIP = "https://github.com/limine-bootloader/limine/archive/refs/heads/v8.x-binary.zip"
XORRISO_ZIP = "https://github.com/PeyTy/xorriso-exe-for-windows/archive/refs/heads/master.zip"
LIMINE_CONF = """timeout: 3

/MORT OS
    protocol: multiboot1
    path: boot():/boot/kernel.elf
    module_path: boot():/boot/welcome.txt
    module_path: boot():/boot/startup.txt
"""

# Files staged onto the ISO and loaded as multiboot modules. Module 0
# (welcome.txt) is shown by `readme`; module 1 (startup.txt) is a script of
# shell commands the kernel runs at boot, like an /etc/rc.
WELCOME_TXT = """Welcome to MORT OS.

You are looking at a file that lived on the ISO, was loaded from disk by the
Limine bootloader as a multiboot module, and is now being read by the kernel.

Everything here -- the kernel, its shell, and the Mort language it is written
in -- was built from scratch. Type 'help' for commands.
"""

STARTUP_TXT = """echo running startup script loaded from disk...
about
"""

# Seeded onto a fresh MortFS disk image so first boot's `ls` shows something.
HELLO_TXT = "this file lives on the MortFS disk\n"


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


def build_program(mx_path, out_bin):
    """Compile one Mort program (programs/*.mx) into a flat binary at out_bin.

    Same proven 4-step recipe as the kernel, but linked with programs/prog.ld
    so the whole image sits at the fixed load base 0x00A00000 with the entry
    stub (pstart.s) first, then objcopy'd to a raw flat binary (no ELF headers).
    Intermediate .c/.o/.elf land in BUILD, never the repo.
    """
    os.makedirs(BUILD, exist_ok=True)
    cc = _zig()
    name = os.path.splitext(os.path.basename(mx_path))[0]

    # 1. Mort program -> freestanding C (main becomes mort_main, which pstart calls).
    with open(mx_path, encoding="utf-8") as fh:
        c_source = mortc.compile_to_c(fh.read(), freestanding=True)
    prog_c = os.path.join(BUILD, name + ".c")
    with open(prog_c, "w", encoding="utf-8") as fh:
        fh.write(c_source)

    c_flags = ["-target", TARGET, "-ffreestanding", "-fno-stack-protector",
               "-fno-pie", "-fno-asynchronous-unwind-tables", "-fno-unwind-tables",
               "-O2"]
    asm_flags = ["-target", TARGET, "-fno-pie"]  # assembling needs no C flags
    prog_o = os.path.join(BUILD, name + ".o")
    pstart_o = os.path.join(BUILD, name + ".pstart.o")
    prog_elf = os.path.join(BUILD, name + ".elf")

    # 2. Compile the program C and assemble the entry stub.
    subprocess.run([*cc, *c_flags, "-c", prog_c, "-o", prog_o], check=True)
    subprocess.run([*cc, *asm_flags, "-c", os.path.join(PROGRAMS, "pstart.s"),
                    "-o", pstart_o], check=True)

    # 3. Link at the fixed load base with the flat-image linker script.
    subprocess.run([
        *cc, "-target", TARGET, "-nostdlib", "-static", "-no-pie",
        "-Wl,-T," + os.path.join(PROGRAMS, "prog.ld"),
        "-Wl,--build-id=none", "-Wl,-e,_pstart",   # entry stub, silences _start warning
        "-o", prog_elf, pstart_o, prog_o,
    ], check=True)

    # 4. Strip ELF headers down to a raw flat binary the kernel loads verbatim.
    objcopy = cc[:-1] + ["objcopy"]  # find_zig() ends in "cc"; swap for "objcopy"
    subprocess.run([*objcopy, "-O", "binary", prog_elf, out_bin], check=True)
    print(f"built {os.path.relpath(out_bin, ROOT)}")


def _program_sources():
    """Return sorted absolute paths of every programs/*.mx source."""
    if not os.path.isdir(PROGRAMS):
        return []
    return [os.path.join(PROGRAMS, n) for n in sorted(os.listdir(PROGRAMS))
            if n.endswith(".mx")]


def prog():
    """Build every kernel/programs/*.mx into BUILD/<name>.bin."""
    sources = _program_sources()
    if not sources:
        sys.exit(f"prog: no *.mx programs found in {os.path.relpath(PROGRAMS, ROOT)}")
    for mx in sources:
        name = os.path.splitext(os.path.basename(mx))[0]
        build_program(mx, os.path.join(BUILD, name + ".bin"))


def ensure_disk():
    """Create the MortFS disk image iff it is missing.

    Files written from inside the OS must survive rebuilds, so an existing
    image is never touched; `python kernel/mkfs.py kernel/build/disk.img` is
    the explicit wipe-and-reseed path. A fresh image is seeded with the text
    file plus the compiled program binaries (added raw, no CRLF normalization),
    so first boot can `exec hello.bin`.
    """
    if os.path.exists(DISK):
        return
    os.makedirs(BUILD, exist_ok=True)
    import mkfs  # lives next to this script; sys.path[0] is kernel/ either way
    seed = os.path.join(BUILD, "hello.txt")
    with open(seed, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(HELLO_TXT)

    # Compile the programs and seed only the ones that build successfully.
    bins = []
    for mx in _program_sources():
        name = os.path.splitext(os.path.basename(mx))[0]
        out_bin = os.path.join(BUILD, name + ".bin")
        build_program(mx, out_bin)
        bins.append((out_bin, name + ".bin"))

    mkfs.make(DISK, adds=[(seed, "hello.txt")], bins=bins)


def disk():
    if os.path.exists(DISK):
        print(f"{os.path.relpath(DISK, ROOT)} already exists — leaving it alone "
              "(in-OS writes survive). Wipe and reseed with: "
              "python kernel/mkfs.py kernel/build/disk.img")
        return
    ensure_disk()


def _fetch_zip(url, dest):
    import io
    import urllib.request
    import zipfile
    os.makedirs(dest, exist_ok=True)
    print(f"  downloading {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "mort-build"})
    with urllib.request.urlopen(req) as r:
        data = r.read()
    zipfile.ZipFile(io.BytesIO(data)).extractall(dest)


def _find(root, name):
    for dirpath, _dirs, files in os.walk(root):
        if name in files:
            return os.path.join(dirpath, name)
    return None


def _cygpath(p):
    """Convert a Windows path to the Cygwin form xorriso.exe expects."""
    p = os.path.abspath(p)
    drive, rest = os.path.splitdrive(p)
    return "/cygdrive/" + drive[0].lower() + rest.replace("\\", "/")


def iso():
    """Build a BIOS+UEFI bootable, USB-writable ISO using Limine + xorriso."""
    build()
    os.makedirs(TOOLS, exist_ok=True)

    # 1. Ensure the (portable, Windows) tools are present — download + cache once.
    if not _find(TOOLS, "limine.exe"):
        _fetch_zip(LIMINE_ZIP, os.path.join(TOOLS, "limine"))
    if not _find(TOOLS, "xorriso.exe"):
        _fetch_zip(XORRISO_ZIP, os.path.join(TOOLS, "xorriso"))
    limine = _find(TOOLS, "limine.exe")
    xorriso = _find(TOOLS, "xorriso.exe")
    if not limine or not xorriso:
        sys.exit("iso: could not locate limine.exe / xorriso.exe after download.")
    lim_dir = os.path.dirname(limine)

    # 2. Stage the ISO tree: kernel + Limine boot files + config.
    root = os.path.join(BUILD, "iso_root")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, "boot"), exist_ok=True)
    os.makedirs(os.path.join(root, "EFI", "BOOT"), exist_ok=True)
    shutil.copy(ELF, os.path.join(root, "boot", "kernel.elf"))
    with open(os.path.join(root, "boot", "limine.conf"), "w", encoding="utf-8") as fh:
        fh.write(LIMINE_CONF)
    with open(os.path.join(root, "boot", "welcome.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(WELCOME_TXT)
    with open(os.path.join(root, "boot", "startup.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(STARTUP_TXT)
    for name in ("limine-bios.sys", "limine-bios-cd.bin", "limine-uefi-cd.bin"):
        src = _find(lim_dir, name)
        if src:
            shutil.copy(src, os.path.join(root, "boot", name))
    for name in ("BOOTX64.EFI", "BOOTIA32.EFI"):
        src = _find(lim_dir, name)
        if src:
            shutil.copy(src, os.path.join(root, "EFI", "BOOT", name))
    if not os.path.exists(os.path.join(root, "boot", "limine-bios-cd.bin")):
        sys.exit("iso: limine-bios-cd.bin not found in the Limine download — "
                 "the release layout may have changed.")

    # 3. Master a hybrid El Torito ISO, then make the BIOS path bootable.
    # xorriso is a Cygwin build, so host paths must be in /cygdrive/... form.
    subprocess.run([
        xorriso, "-as", "mkisofs", "-R", "-r", "-J",
        "-b", "boot/limine-bios-cd.bin",
        "-no-emul-boot", "-boot-load-size", "4", "-boot-info-table",
        "--efi-boot", "boot/limine-uefi-cd.bin",
        "-efi-boot-part", "--efi-boot-image", "--protective-msdos-label",
        _cygpath(root), "-o", _cygpath(ISO),
    ], check=True)
    subprocess.run([limine, "bios-install", ISO], check=True)
    print(f"built {os.path.relpath(ISO, ROOT)}")
    print(f"boot it with:  python kernel/build.py run-iso   (or -cdrom {os.path.basename(ISO)})")


def run_iso():
    iso()
    ensure_disk()
    qemu = _find_qemu()
    if not qemu:
        sys.exit("qemu-system-i386 not found — install QEMU to boot the ISO.")
    print("Booting the MORT OS ISO in QEMU...")
    subprocess.run([qemu, "-cdrom", ISO, "-hda", DISK])


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


def _run(fullscreen):
    build()
    ensure_disk()
    qemu = _find_qemu()
    if not qemu:
        sys.exit("qemu-system-i386 not found — install QEMU (e.g. `winget install "
                 "SoftwareFreedomConservancy.QEMU`) to boot the kernel.")
    # GTK display: hide the menu bar and scale the 80x25 console to the window.
    display = "gtk,zoom-to-fit=on,show-menubar=off"
    cmd = [qemu, "-display", display, "-kernel", ELF, "-hda", DISK]
    if fullscreen:
        cmd.insert(1, "-full-screen")
        print("Booting MORT OS fullscreen. Ctrl+Alt+F toggles fullscreen, "
              "Ctrl+Alt+G releases the mouse, Ctrl+Alt+Q quits.")
    else:
        print("Booting MORT OS. Maximise the window to scale it up; "
              "Ctrl+Alt+G releases the mouse; close the window to exit.")
    # A list argv lets subprocess quote the (space-containing) ELF path for us.
    subprocess.run(cmd)


def run():
    _run(fullscreen=True)


def run_windowed():
    _run(fullscreen=False)


COMMANDS = {"build": build, "check": check, "run": run, "window": run_windowed,
            "iso": iso, "run-iso": run_iso, "disk": disk, "prog": prog}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd not in COMMANDS:
        sys.exit(f"unknown command {cmd!r}; use one of: {', '.join(COMMANDS)}")
    COMMANDS[cmd]()
