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
x86 multiboot kernel. `run` boots it; you'll see **MORT OS BOOTED** in green,
then anything you type appears on screen (letters, space, Enter for a new line).

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
- [ ] A PIT timer (IRQ0) for `uptime`/delays — a second interrupt source.
- [ ] CPU exception handlers that report the fault (a stub halts cleanly today).
