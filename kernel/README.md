# MORT OS

A tiny operating-system kernel **written in Mort** — it boots on QEMU, runs in
32-bit protected mode, and gives you an **interactive shell**: type a command,
edit it with Backspace, press Enter to run it (`help`, `clear`). Everything —
VGA output, PS/2 keyboard input, and command parsing — is written in Mort.

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
python kernel/build.py run     # build, then boot it in QEMU
```

`check` needs no QEMU — it builds `build/kernel.elf` and confirms it's a 32-bit
x86 multiboot kernel. `run` boots it fullscreen; type `help` to see the commands.

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
- [ ] A disk driver + real filesystem; executing loaded programs.
