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

- **`kmain.mx`** — the whole OS, written in Mort: VGA output, a polled PS/2
  keyboard driver (`inb` from ports `0x64`/`0x60`), scancode→ASCII, a `streq`
  for parsing, and a shell loop with Backspace editing and `help`/`clear`
  commands. The typed line is buffered in a mutable string literal used as
  scratch memory.
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
x86 multiboot kernel. `run` boots it; you'll see **MORT OS BOOTED** in green,
then anything you type appears on screen (letters, space, Enter for a new line).

## Status & roadmap

- [x] Boots in QEMU and prints to VGA text mode.
- [x] A `print_string` routine written in Mort using string literals (`*u8`).
- [x] `inb`/`outb` port-I/O builtins.
- [x] Polled PS/2 keyboard input, echoing keystrokes to the screen.
- [x] A shell: Backspace line editing, a command parser, and `help`/`clear`.
- [ ] Shift/caps for uppercase and symbols; digits and punctuation.
- [ ] Screen scrolling instead of wipe-on-overflow.
- [ ] Interrupt-driven input (IDT + PIC) instead of polling.
