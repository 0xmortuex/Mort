# MORT OS

A tiny operating-system kernel **written in Mort** — it boots on QEMU, runs in
32-bit protected mode, and gives you an **interactive shell with a real
filesystem**: write files, `cat` them, delete them, run them as scripts — and
they **survive a reboot**. It even **runs real compiled programs**: a `.mx`
program compiled to a flat binary, loaded off the disk, and talking to the
kernel through `int 0x80` syscalls. Everything — VGA output, PS/2 keyboard
input, the ATA disk driver, the filesystem, the syscall layer, and command
parsing — is written in Mort.

```
┌─────────────────────────────────────────┐
│  MORT OS -- type 'help', then Enter     │
│                                         │
│  > help                                 │
│  commands: help, clear                  │
│  > _                                    │
│                                         │
└─────────────────────────────────────────┘
        QEMU, booted from kernel.elf
```

## How it fits together

```
kmain.mx    the kernel, in Mort        ─┐
            │  mortc --freestanding      │  Mort -> freestanding C -> 32-bit object
            ▼                            │
boot.s      multiboot header + _start   ─┤  assembled to a 32-bit object
            │                            │
linker.ld   places it at 1 MB           ─┘  linked ->  build/kernel.elf
            │
            ▼
qemu-system-i386 -kernel build/kernel.elf
```

- **`kmain.mx`** — the OS in Mort: VGA output, scancode→ASCII (Shift-aware), a
  `streq` for parsing, and an `on_key` interrupt handler that drives the shell
  (`help`/`clear`, Backspace editing). Shell state lives in Mort globals so it
  survives between interrupts. `kmain` sets up, enables interrupts, and idles.
- **`idt.s`** — a flat GDT, a PIC remap (IRQs → vectors 0x20+), and an IDT whose
  keyboard gate (IRQ1) calls `mort_on_key`. `kernel_setup` wires it all up.
- **`boot.s`** — a multiboot1 header so QEMU recognises the file, plus a `_start`
  stub that sets up a stack and calls `mort_kmain`.
- **`linker.ld`** — loads the kernel at the 1 MB mark with the multiboot header
  first.
- **`build.py`** — compiles the Mort kernel to freestanding C, cross-compiles it
  and the boot stub to 32-bit x86 with the Zig backend, and links them.

## Build & run

Requirements: `pip install ziglang` (the C cross-compiler) and
[QEMU](https://www.qemu.org/) for booting.

```bash
python kernel/build.py check   # build and verify it's a valid multiboot ELF
python kernel/build.py run     # build, then boot it in QEMU (with the disk)
```

`check` needs no QEMU — it builds `build/kernel.elf` and confirms it's a 32-bit
x86 multiboot kernel. `run` boots it fullscreen; type `help` to see the commands.

### The disk

`run` auto-creates `kernel/build/disk.img` (16 MiB, MortFS) on first boot and
attaches it with `-hda`, so files you `write` in the OS persist across reboots.
`python kernel/mkfs.py kernel/build/disk.img` wipes it clean (add
`--add host.txt:name.txt` to seed files from the host). Try:

```
> write notes.txt hello from mort os
> cat notes.txt
> write job.txt echo hi
> write job.txt uptime
> run job.txt
```

### Running programs

MORT OS runs real compiled programs, not just shell scripts. A program is a
Mort source file compiled to a **flat 32-bit binary** loaded at `0x00A00000`;
it shares no symbols with the kernel and talks to it only through **`int 0x80`
syscalls** (arguments passed via a fixed mailbox at `0x009F0000`, since Mort's
`asm()` takes no operands). Sample programs live in `kernel/programs/`.

```bash
python kernel/build.py prog      # compile programs/*.mx -> build/*.bin
```

`build.py disk` seeds the compiled programs onto the image, so from the shell:

```
> ls
> exec hello.bin      # a real Mort program prints via syscall, then returns
```

Automated tests (all drive the real kernel headless in QEMU — inject keys
through the monitor, read VGA memory back):

```bash
python kernel/test.py smoke      # boot + shell basics
python kernel/test_fs.py         # the disk stack, incl. write-reboot-cat persistence
python kernel/test_exec.py       # build programs, seed, boot, exec, check syscall output
```

### A real bootable ISO

```bash
python kernel/build.py iso       # -> kernel/build/mort.iso
python kernel/build.py run-iso   # build the ISO and boot it in QEMU
```

`iso` produces a genuinely bootable image using the [Limine](https://limine-bootloader.org/)
bootloader (which loads our multiboot1 kernel unchanged) and `xorriso`. Both are
portable Windows binaries, downloaded and cached under `kernel/tools/` on first
run — no WSL or MSYS2 needed. The result, `mort.iso`, is a BIOS **and** UEFI
hybrid image: boot it in QEMU (`-cdrom`) or VirtualBox, or write it byte-for-byte
to a USB stick (e.g. Rufus in "DD image" mode) and boot it on real hardware.

## Status & roadmap

- [x] Boots in QEMU and prints to VGA text mode.
- [x] A `print_string` routine written in Mort using string literals (`*u8`).
- [x] `inb`/`outb` port-I/O builtins.
- [x] Polled PS/2 keyboard input, echoing keystrokes to the screen.
- [x] A shell: Backspace line editing, a command parser, and `help`/`clear`.
- [x] Shift for uppercase, digits, and punctuation.
- [x] Interrupt-driven input: a GDT/IDT and remapped PICs; the keyboard fires
      IRQ1 into a Mort handler instead of being polled.
- [x] A blinking hardware cursor that tracks the input, and more commands
      (`about`, `echo <text>`).
- [x] Terminal-style screen scrolling when output reaches the bottom row.
- [x] A PIT timer on IRQ0 (~100 Hz) — a second interrupt source, with an
      `uptime` command and decimal number printing.
- [x] Per-vector CPU exception handlers that report which fault occurred (try
      the `crash` command); each stub records its vector before halting.
- [x] Command history: Up/Down arrows recall previous commands (a ring of 8),
      decoded from the 0xE0 extended-scancode prefix.
- [x] A real bootable ISO (Limine + xorriso) — BIOS/UEFI hybrid, USB-writable,
      boots on real hardware, not just QEMU's `-kernel` shortcut.
- [x] Loading a file from disk: the bootloader loads a file off the ISO as a
      multiboot module; the kernel reads the multiboot info (passed in EBX) and
      the `readme` command prints the file's contents.
- [x] Running a program from disk: a second module is a script of shell
      commands that the kernel executes at boot, like an /etc/rc.
- [x] **An ATA PIO disk driver** (LBA28, polling, graceful no-disk fallback)
      — the first Mort code to drive a block device.
- [x] **MortFS, a real filesystem**: superblock + file table + 64 KiB extents,
      with `ls`, `cat <f>`, `write <f> <text>` (append), `rm <f>` — and files
      **persist across reboots**. Host-side `mkfs.py` creates/seeds images.
- [x] `run <f>`: execute a file of shell commands — author a script inside
      the OS with `write`, then run it.
- [x] **Executing real compiled programs**: a Mort program built to a flat
      binary, loaded off the disk to `0x00A00000`, entered with a `call`, and
      served through `int 0x80` syscalls — `exec <file>`. See `programs/`.
- [x] An automated QEMU test harness (`test.py`, `test_fs.py`, `test_exec.py`):
      boots the kernel headless, types via the monitor, asserts on VGA memory.
- [ ] Space reclamation for `rm` (v1 leaks the extent; re-mkfs to compact).
- [ ] More syscalls (input, file I/O from programs) and a richer program ABI.
