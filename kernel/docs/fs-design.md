# MORT OS — Disk + Filesystem Design (MortFS v1)

Design document. Nothing here is implemented yet; this is the spec to implement
from. Scope: an ATA PIO disk driver, a brutally simple on-disk filesystem
(MortFS v1), fixed kernel buffers, five shell commands, and a host-side
`mkfs.py` tool with `build.py` integration.

Everything below was designed against the *actual* Mort compiler
(`mort/typechecker.py`, `mort/codegen.py`) and the actual kernel
(`kernel/kmain.mx`), verified by test-compiling probe code in freestanding
mode. Section 0 records what was verified so the implementer does not have to
re-derive it.

---

## 0. Verified language ground rules

These were confirmed by reading the compiler and compiling a freestanding
probe (`--freestanding --emit-c`):

| Capability | Status | Evidence |
|---|---|---|
| `u16` type | works | `mort/parser.py` FIXED_INT_TYPES, `typechecker.py` INT_RANGES |
| `inb(port)->u8`, `outb(port,u8)` | works | typechecker lines 626–643; codegen emits inline asm helpers |
| **`inw` / `outw`** | **MISSING — hard prerequisite** | not in `BUILTIN_NAMES`; being added in parallel. This design assumes `inw(port: u16) -> u16` and `outw(port: u16, val: u16)`, mirroring `inb`/`outb`. If `inw` lands returning `u32` instead, the only change in this design is dropping one `as u32` cast in `ata_read`. **The ATA data register (0x1F0) is a 16-bit register; two `inb`s are NOT a substitute.** Nothing in Section 1 can ship before this lands. |
| Global mutable scalars | works | used throughout kmain.mx |
| **Global arrays** (`let g: [u8; 512] = [0; 512];`) | **works** (contrary to what kmain.mx's string-literal buffers suggest) | typechecker line 131 explicitly accepts ArrayLit/ArrayRepeat globals; probe compiled to `static uint8_t m_g[512] = {0, ...};` |
| `(&arr) as u32` / `(&arr) as *u8` | works | `&` on a Var lvalue yields `*[u8;512]`, which is a pointer and castable |
| `&arr[0]` | **illegal** | `_is_lvalue` (typechecker ~line 429) accepts only Var / deref / FieldAccess — Index is not an lvalue. Use `(&arr) as u32 + i` instead. |
| `arr as u32` (no `&`) | illegal | Cast source must be int or pointer; array types are neither |
| Structs in freestanding | compile fine (plain C structs) | probe compiled; but **not used in this design** — raw offset arithmetic matches the existing multiboot idiom and avoids depending on C struct layout for on-disk data |
| `<<` `>>` `&` `|` `^` `%` | work | typechecker binary ops |
| Writable string literals | works (emitted as `static uint8_t mort_str_N[]`) | codegen line 138 + comment; kmain.mx relies on this for g_buf |
| Heap / malloc | none | fixed addresses or globals only |
| Modules/imports | none | everything goes into kmain.mx |

One codegen caveat: a global array's repeat initialiser is expanded
element-by-element in the generated C (`[0; 65536]` → 65536 literal zeros).
Small arrays are fine; do **not** declare the 64 KB file buffer as a global
array. This design puts all large buffers at fixed physical addresses
(Section 3), which is also the existing kernel idiom.

Naming/style: follow kmain.mx — `g_` prefix for globals, raw
`*((addr) as *u8)` loads/stores, `fn name(args)` with explicit `u32`
loop counters, comments explaining hardware magic numbers.

---

## 1. ATA PIO driver (primary bus, master drive, LBA28, polling)

No IRQs: the PIC master mask is already `0xFC` (only IRQ0/IRQ1 unmasked, see
`idt.s` `remap_pic`), so ATA IRQ14 never fires anyway. Additionally set nIEN
in the device control register at init to be explicit.

### 1.1 Port map

| Port | Name | Direction | Notes |
|---|---|---|---|
| `0x1F0` | Data | R/W, **16-bit** | `inw`/`outw` only |
| `0x1F1` | Error / Features | R / W | read on ERR for diagnostics (optional) |
| `0x1F2` | Sector count | W | we always write `1` |
| `0x1F3` | LBA bits 0–7 | W | |
| `0x1F4` | LBA bits 8–15 | W | |
| `0x1F5` | LBA bits 16–23 | W | |
| `0x1F6` | Drive/head select | W | `0xE0 | ((lba >> 24) & 0x0F)` = LBA mode, master, LBA bits 24–27 |
| `0x1F7` | Status (R) / Command (W) | R/W | commands: `0x20` READ SECTORS, `0x30` WRITE SECTORS, `0xE7` CACHE FLUSH |
| `0x3F6` | Device control (W) / **Alternate status (R)** | R/W | Verified: the primary bus control block base is `0x3F6`. Reading it returns the same bits as `0x1F7` but does **not** ack a pending interrupt. Writing `0x02` sets nIEN (IRQs off). |

Status register bits (0x1F7 / 0x3F6):

```
0x80 BSY   drive busy — no other bit is valid while set
0x40 RDY   drive ready
0x20 DF    device fault
0x08 DRQ   data ready to transfer
0x01 ERR   error (details in 0x1F1)
```

### 1.2 The 400 ns delay

After writing the drive-select register (0x1F6) the drive needs ~400 ns
before its status is valid. Standard technique: read the **alternate status**
port four times and discard the result (each I/O read takes ~100 ns):

```
fn ata_delay() {
    inb(0x3F6);
    inb(0x3F6);
    inb(0x3F6);
    inb(0x3F6);
}
```

(Reading 0x1F7 four times also works, but alt-status is the canonical choice
because it never clears interrupt state.)

### 1.3 Globals and constants

```
let g_disk_ok: bool = false;      // set once by ata_init(); every command checks it
```

Timeout constant: `1000000` poll iterations. In QEMU a sector op completes in
a handful of iterations; on real hardware one `inb` is ~1 µs, so this is a
~1 s ceiling. On timeout the operation fails cleanly instead of hanging the
interrupt handler forever.

### 1.4 Function signatures and exact sequences

All functions return `bool` success so the shell can degrade gracefully
(booted without `-hda`, drive fault, timeout).

```
fn ata_delay()                             // 4x inb(0x3F6), see 1.2
fn ata_wait_bsy() -> bool                  // poll until BSY clear; false on timeout
fn ata_wait_drq() -> bool                  // poll until DRQ set; false on timeout/ERR/DF
fn ata_init() -> bool                      // detect drive, set nIEN; sets g_disk_ok
fn ata_read(lba: u32, buf: *u8) -> bool    // one 512-byte sector, disk -> buf
fn ata_write(lba: u32, buf: *u8) -> bool   // one 512-byte sector, buf -> disk (+ flush)
```

**ata_wait_bsy** — spin with a bounded counter:

```
fn ata_wait_bsy() -> bool {
    let i: u32 = 0;
    while i < 1000000 {
        let st: u8 = inb(0x1F7);
        if (st & 0x80) == 0 { return true; }
        i = i + 1;
    }
    return false;
}
```

**ata_wait_drq** — BSY must already be clear or clearing; fail fast on
ERR/DF so a bad command can't spin the full timeout:

```
fn ata_wait_drq() -> bool {
    let i: u32 = 0;
    while i < 1000000 {
        let st: u8 = inb(0x1F7);
        if (st & 0x80) == 0 {            // BSY clear -> other bits valid
            if (st & 0x21) != 0 { return false; }   // ERR or DF
            if (st & 0x08) != 0 { return true; }    // DRQ
        }
        i = i + 1;
    }
    return false;
}
```

**ata_init** — detection. With no drive attached the bus floats and every
status read returns `0xFF`; that is the primary "no disk" signal. Sequence:

1. `outb(0x1F6, 0xE0)` — select master, LBA mode.
2. `ata_delay()`.
3. `let st: u8 = inb(0x1F7);`
4. If `st == 0xFF` → floating bus, no drive: `g_disk_ok = false; return false;`
5. `if !ata_wait_bsy() { g_disk_ok = false; return false; }`
6. Re-read status; require `(st & 0x40) != 0` (RDY) → else absent/broken.
7. `outb(0x3F6, 2);` — set nIEN (no ATA interrupts; belt-and-braces, IRQ14 is masked anyway).
8. `g_disk_ok = true; return true;`

Called once from `kmain` before `fs_init()` (Section 2.4). Every `ata_read`/
`ata_write` starts with `if !g_disk_ok { return false; }`.

**ata_read(lba, buf)** — READ SECTORS (0x20), one sector:

```
if !g_disk_ok      -> return false
if !ata_wait_bsy() -> return false
outb(0x1F6, (0xE0 | ((lba >> 24) & 0x0F)) as u8)
ata_delay()
outb(0x1F2, 1)                         // sector count = 1
outb(0x1F3, (lba & 0xFF) as u8)
outb(0x1F4, ((lba >> 8) & 0xFF) as u8)
outb(0x1F5, ((lba >> 16) & 0xFF) as u8)
outb(0x1F7, 0x20)                      // READ SECTORS
if !ata_wait_drq() -> return false
// 256 words; the data register is little-endian: low byte first
let i: u32 = 0;
while i < 256 {
    let w: u32 = inw(0x1F0) as u32;
    *((buf as u32 + i * 2) as *u8)     = (w & 0xFF) as u8;
    *((buf as u32 + i * 2 + 1) as *u8) = ((w >> 8) & 0xFF) as u8;
    i = i + 1;
}
return true
```

**ata_write(lba, buf)** — WRITE SECTORS (0x30) + CACHE FLUSH (0xE7):

```
if !g_disk_ok      -> return false
if !ata_wait_bsy() -> return false
outb(0x1F6, (0xE0 | ((lba >> 24) & 0x0F)) as u8)
ata_delay()
outb(0x1F2, 1)
outb(0x1F3, (lba & 0xFF) as u8)
outb(0x1F4, ((lba >> 8) & 0xFF) as u8)
outb(0x1F5, ((lba >> 16) & 0xFF) as u8)
outb(0x1F7, 0x30)                      // WRITE SECTORS
if !ata_wait_drq() -> return false
let i: u32 = 0;
while i < 256 {
    let lo: u32 = *((buf as u32 + i * 2) as *u8) as u32;
    let hi: u32 = *((buf as u32 + i * 2 + 1) as *u8) as u32;
    outw(0x1F0, (lo | (hi << 8)) as u16);
    i = i + 1;
}
if !ata_wait_bsy() -> return false
outb(0x1F7, 0xE7)                      // CACHE FLUSH
if !ata_wait_bsy() -> return false
return true
```

Interrupt context note: all disk I/O runs inside the keyboard ISR (interrupt
gates clear IF, so IRQs are off for the duration). Polling PIO does not need
interrupts; the only side effect is that PIT ticks are lost while a command
runs (`uptime` drifts slightly during heavy disk use). Accepted for v1.

---

## 2. MortFS v1 on-disk format

Design goals, in order: implementable in Mort with raw offset arithmetic (no
structs, no heap), fixed everything, honest about limitations.

All multi-byte integers are **little-endian u32** — which is exactly what a
`*((addr) as *u32)` load/store produces on x86, so the kernel never
byte-swaps anything, and Python's `struct.pack('<I', ...)` matches.

### 2.1 Disk layout (16 MiB image = 32768 sectors)

```
sector 0          superblock
sectors 1..8      file table (64 entries x 64 bytes = 4096 bytes)
sectors 9..15     reserved (zeros; future use)
sectors 16..32767 data region (contiguous per-file extents)
```

### 2.2 Superblock (sector 0)

| Offset | Size | Field | Value |
|---|---|---|---|
| 0 | u32 | magic | `0x3153464D` — the bytes `"MFS1"` (`4D 46 53 31`) read as a little-endian u32 |
| 4 | u32 | version | `1` |
| 8 | u32 | file_count | number of `used == 1` table entries |
| 12 | u32 | next_free_sector | bump allocator; starts at `16` |
| 16 | u32 | total_sectors | `32768` for the default 16 MiB image |
| 20..511 | — | reserved | zeros |

### 2.3 File table entry (64 bytes each; entry `i` lives at byte `512 + i*64` of the metadata region, i.e. sector `1 + i/8`, offset `(i % 8) * 64`)

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 24 bytes | name | ASCII, NUL-padded. **Max name length 23** + terminating NUL. |
| 24 | u32 | used | `1` = live, `0` = free slot |
| 28 | u32 | size_bytes | current file size |
| 32 | u32 | start_sector | first sector of this file's extent |
| 36 | u32 | capacity_sectors | always written as `128` in v1 (see 2.5) |
| 40..63 | — | reserved | zeros |

### 2.4 Fixed limits and their justification

| Limit | Value | Why |
|---|---|---|
| Max files | 64 | 64 × 64 B = exactly 8 sectors of table; `ls` output of 64 lines is already 2.5 screenfuls on an 80×25 console — more would be unusable anyway |
| Max filename | 23 chars | fits the 24-byte NUL-padded field; with the widened 76-char input line (Section 2.7), `write ` (6) + name (23) + space (1) still leaves 46 chars of text — enough for a useful line. `cat longest-legal-name.txt` is 4 + 23 = 27 chars, comfortable. |
| Max file size | 64 KiB (131072 would not fit the file buffer; 65536 bytes = 128 sectors) | equals the fixed file I/O buffer (Section 3), so whole-file read/append never partial-fills; 64 KiB of shell-script text is ~1000 lines — far beyond what this OS needs |
| Fixed extent per file | 128 sectors | see 2.5 |
| Image size | 16 MiB | tiny on the host, and `(32768 - 16) / 128 = 255` lifetime file creations before the bump pointer hits the end — plenty (see 2.6) |

### 2.5 Allocation: fixed 128-sector extents, bump pointer

Every created file is allocated **exactly 128 contiguous sectors (64 KiB)**
at `next_free_sector`, which then bumps by 128. `capacity_sectors` is still
written per entry (always 128) so a future version can vary it without a
format change; the kernel must *read* the field rather than hard-coding 128.

Why fixed-size extents: it makes **append** trivially safe (a file can always
grow to the max file size in place), removes all fragmentation logic, and
costs nothing at this scale (64 live files × 64 KiB = 4 MiB of a 16 MiB
image).

### 2.6 Deletion — documented limitation

`rm` sets `used = 0` and decrements `file_count`. **The extent is not
reclaimed**: the bump pointer never rewinds and there is no free-extent list
or compaction. Space leaks by 64 KiB per create-after-delete cycle; after 255
total creations the disk reports full even if `ls` shows few files. The fix
is re-running `mkfs.py` on the host (or a future `fsck`/compaction pass).
This is a deliberate v1 trade: reclamation is the single biggest source of
filesystem bugs, and the whole FS state fits in two fixed buffers as-is.

When the disk is full, `fs_create` fails with `disk full` — files already on
disk remain readable/appendable/removable.

### 2.7 PREREQUISITE: widen the shell input line to 76 chars

The current line buffer is 40 bytes with a 30-char guard
(`g_buf`, kmain.mx line 14). `write notes.txt hello world from mort os` is
already 38 chars — it does not fit. **Part of this milestone**: grow the line
to 76 chars (one VGA row: prompt occupies columns 0–1, input columns 2..77,
cursor parks at 78 without wrapping). Concretely, in kmain.mx:

1. `g_buf` literal (line 14): 40 spaces → **80 spaces** (76 chars + NUL + slack).
2. `g_history` literal (line 22): 320 spaces → **640 spaces** (8 slots × 80).
3. `hist_slot` (line 216) and `hist_store` (line 220): slot stride `40` → `80`.
4. `set_input` guard (lines 234–236): `30` → `76`.
5. `run_script` line-length guard (line 340): `30` → `76`.
6. `on_key` typing guard (line 555): `30` → `76`.

Define the limit once as a comment convention (`// MAXLINE = 76`) since Mort
has no named constants; every literal `76` gets a `// MAXLINE` comment.

### 2.8 Kernel FS functions (raw offset arithmetic, no structs)

The 9 metadata sectors (superblock + table) are cached in RAM at `TABCACHE`
(Section 3) by `fs_init` and written back wholesale by `fs_sync`. All
"parsing" is offset math against that cache, exactly like the existing
multiboot code (`module_start`, `mem`).

Helper address map (with `TABCACHE = 0x00801000`):

```
superblock field F        -> *((0x00801000 + F) as *u32)
entry i, u32 field at off -> *((0x00801000 + 512 + i * 64 + off) as *u32)
entry i name              -> (0x00801000 + 512 + i * 64) as *u8
```

Signatures (`64` doubles as the "no entry" sentinel since valid indices are
0..63):

```
fn fs_init() -> bool                          // ata_init + read sectors 0..8 into TABCACHE,
                                              // verify magic+version; sets g_fs_ok
fn fs_sync() -> bool                          // write TABCACHE back to sectors 0..8
fn fs_entry_addr(i: u32) -> u32               // 0x00801000 + 512 + i * 64
fn fs_find(name: *u8) -> u32                  // index of used entry with this name, else 64
fn fs_free_slot() -> u32                      // index of first used==0 entry, else 64
fn fs_create(name: *u8) -> u32                // claim slot + 128-sector extent; 64 on failure
fn fs_read_file(i: u32) -> u32                // whole extent's live sectors -> FILEBUF;
                                              // returns size_bytes (0-size files return 0)
fn fs_append_line(i: u32, text: *u8) -> bool  // append text + '\n' (see below)
fn fs_remove(name: *u8) -> bool               // mark unused, file_count--, fs_sync
```

Globals:

```
let g_fs_ok: bool = false;    // magic verified; every command checks it
```

`fs_append_line` algorithm (works sector-at-a-time through SECBUF so partial
last sectors are read-modify-written correctly):

1. `let size: u32 = size_bytes(i);` `let n: u32 = str_len(text);`
2. If `size + n + 1 > capacity_sectors(i) * 512` → false (`file full`).
3. Read the file's current last sector (`start + size / 512`) into SECBUF
   (skip the read if `size % 512 == 0` — fresh sector, just zero SECBUF).
4. Copy bytes of `text` then a `\n` (byte 10) into SECBUF at `size % 512`,
   writing each full SECBUF to disk and moving to the next sector as offsets
   cross 512.
5. Update `size_bytes(i) = size + n + 1`; `fs_sync()`.

`fs_create`: checks in order — name length ≤ 23 (`name too long`), name not
already present (callers decide: `write` treats existing as append, so it
calls `fs_find` first), `fs_free_slot() != 64` (`file table full`),
`next_free_sector + 128 <= total_sectors` (`disk full`). On success: copy
name (NUL-pad to 24), `used=1`, `size_bytes=0`, `start_sector=next_free`,
`capacity_sectors=128`, bump `next_free_sector`, `file_count++`, `fs_sync`,
return the index.

`fs_init` failure modes: no disk (`g_disk_ok` false) → silent, commands say
`no disk (boot with -hda disk.img)`; disk present but wrong magic →
commands say `bad filesystem (run: python kernel/mkfs.py disk.img)`.
Distinguish with two flags: `g_disk_ok` (ATA) and `g_fs_ok` (magic).

---

## 3. Kernel memory map for buffers

Fixed physical addresses, following the existing "raw pointers into free RAM"
idiom. Chosen region: **8 MiB**, for margin:

- The kernel links at 1 MiB (`linker.ld: . = 1M;`) and its image + bss is
  well under 1 MiB.
- Multiboot info, the memory map, and module *descriptors* sit in low memory;
  the module *contents* (welcome.txt, startup.txt — a few hundred bytes) are
  placed by QEMU/Limine just above the kernel image, i.e. below 2 MiB in
  practice. That placement is not contractual, which is why we skip the
  2 MiB zone entirely and go to 8 MiB — nothing the bootloader does reaches
  there for images this small.
- QEMU's default RAM is 128 MiB; even `-m 16` keeps 8 MiB + 64 KiB in bounds.

| Address | Size | Name | Purpose |
|---|---|---|---|
| `0x00800000` | 512 B | `SECBUF` | single-sector scratch for `ata_read`/`ata_write` callers (append's read-modify-write) |
| `0x00801000` | 4608 B (9 sectors) | `TABCACHE` | in-RAM copy of superblock + file table; all metadata reads hit this, `fs_sync` writes it back |
| `0x00810000` | 64 KiB | `FILEBUF` | whole-file I/O: `cat`, `run`, and `mkfs`-seeded reads land here |

Gaps between them are deliberate slack (TABCACHE could grow to 16 sectors,
FILEBUF sits on a clean 64 KiB boundary). Since Mort has no named constants,
each address appears as a literal with a `// SECBUF` / `// TABCACHE` /
`// FILEBUF` comment, same convention as `0xB8000` today.

Optional hardening (recommended, one line in `fs_init`): if
`module_end(1) > 0x00800000`, print a warning — catches a future world where
modules grow huge.

---

## 4. Shell command specs

All output follows the existing pattern: `feed()` to a new line, `print_at`
/ `print_string`, color 7 for output, 12 (light red) for errors, 15 for
echoed user content. Every FS command first checks, in order:

- `!g_disk_ok` → `no disk (boot with -hda disk.img)` (color 12)
- `!g_fs_ok`  → `bad filesystem (run mkfs on the host)` (color 12)

### 4.1 `ls` — list MortFS files (takes over the old name)

The old module-listing `ls` is **renamed to `mods`** (kept verbatim, just the
command string changes). Rationale: `ls` is the name users reach for, and
real files are now the primary object; modules are a boot mechanism.

Output: one file per line, `name` at column 2 (color 15), size right after at
column 27 (color 7) — `name` panel is 24 wide, so sizes align:

```
> ls
  notes.txt                12 bytes
  startup.txt              47 bytes
```

Empty FS: `(no files)` (color 7). Iterate entries 0..63, print `used == 1`
ones. 64 lines max scrolls the screen; acceptable (same behavior as any long
output today).

### 4.2 `cat <name>`

- Parse: `starts_with(cmd, "cat ")`, name = `(cmd as u32 + 4) as *u8`.
- `fs_find` → if 64: `not found: ` + name (color 12).
- `fs_read_file(i)` into FILEBUF, then
  `print_range(0x00810000, 0x00810000 + size, 7)` — `print_range` already
  handles `\n` and scrolling (kmain.mx line 267).
- Empty file prints nothing (just the fresh prompt).

### 4.3 `write <name> <text>` — create, or **append** to, a file

- Parse: `starts_with(cmd, "write ")`; name starts at `cmd + 6` and ends at
  the next space; text is everything after that space. No second space →
  `usage: write <name> <text>` (color 12). (Parsing needs a small
  `find_char(s: *u8, ch: u8) -> u32` helper returning offset-or-len; the name
  must be NUL-terminated in place by overwriting the space — safe, `cmd` is
  `g_buf` and already consumed.)
- If `fs_find(name) == 64` → `fs_create(name)` (may fail: `name too long`,
  `file table full`, `disk full` — print exactly those, color 12).
- `fs_append_line(i, text)` → on false: `file full` (color 12).
- Success prints nothing (Unix-quiet); the next `ls`/`cat` is the feedback.

**Append semantics, justified**: the input line is single-line, so
*overwrite* semantics would make multi-line files impossible to author
in-OS — killing the best demo this milestone enables
(`write s.txt echo hi` / `write s.txt uptime` / `run s.txt`). Append + `rm`
covers overwrite (rm then write); overwrite cannot cover append. Each `write`
appends `text` plus a trailing `\n`, so files are always well-formed line
sequences for `cat` and `run`.

### 4.4 `rm <name>`

- Parse: `starts_with(cmd, "rm ")`, name at `cmd + 3`.
- `fs_remove` → not found: `not found: ` + name (color 12). Success: silent.
- Reminder in help/docs: space is not reclaimed (Section 2.6).

### 4.5 `run <name>` — execute a file of shell commands

Reuses the existing script machinery by **splitting `run_script` in two**:

```
fn run_range_lines(start: u32, end: u32)   // the existing loop body of run_script,
                                           // reading bytes from [start, end)
fn run_script(index: u32)                  // now: resolve module -> run_range_lines
fn run_file(name: *u8)                     // fs_find + fs_read_file -> run_range_lines(
                                           //   0x00810000, 0x00810000 + size)
```

`run_range_lines` is byte-for-byte the current `run_script` loop (kmain.mx
lines 322–348) with `start`/`end` as parameters — it already copies each line
into `g_buf`, echoes it at a prompt, and calls `run_command`. Note the guard
constant inside it becomes 76 (Section 2.7).

**Ordering constraint**: `fs_read_file` must complete before the first line
is copied into `g_buf`, because the `name` argument points *into* `g_buf`
(it is a suffix of the typed command). Resolve the entry index and finish the
read first; only then start reusing `g_buf` for script lines. Same pattern
the boot-time `run_script(1)` already survives.

**Reentrancy guard**: a script line that says `run other.txt` would clobber
FILEBUF (the outer script's text) and `g_buf` mid-iteration. v1 forbids it:

```
let g_run_depth: u32 = 0;
```

`run_file` checks `g_run_depth > 0` → `run: nested run not allowed`
(color 12); otherwise increments, runs, decrements. The boot-time
`run_script(1)` path does not touch FILEBUF, so a `run` command inside
startup.txt works (depth 0 → 1).

Errors: `not found: <name>` (color 12).

### 4.6 `help` and `mods`

- `help` line becomes (fits in 80 columns):

  ```
  help clear about echo uptime crash readme mem ls cat write rm run mods reboot
  ```

  That is 77 chars — fits with 3 to spare. If another command is ever added,
  split help onto two lines.
- `mods`: the old `ls` body unchanged (module count + sizes).

---

## 5. Host-side tooling

### 5.1 `kernel/mkfs.py` (stdlib only)

```
python kernel/mkfs.py disk.img                              # empty 16 MiB MortFS
python kernel/mkfs.py disk.img --size 16                    # size in MiB (default 16)
python kernel/mkfs.py disk.img --add host.txt:oskernel-name.txt --add b.txt:b.txt
```

Behavior:

- Creates (or **overwrites** — state this in `--help`) `disk.img` of
  `size * 1024 * 1024` bytes, zero-filled.
- Writes the superblock with `struct.pack("<IIIII", 0x3153464D, 1,
  file_count, next_free, total_sectors)` at offset 0.
- For each `--add HOST:NAME` (`NAME` defaults to `basename(HOST)` if the
  `:NAME` part is omitted):
  - read HOST as bytes; normalize `\r\n` → `\n` (the kernel's `print_range`
    skips `\r`, but `run` line-parsing is cleaner without them; matches
    build.py's `newline="\n"` habit);
  - validate: NAME ≤ 23 ASCII chars (error out otherwise), size ≤ 65536
    bytes (error out — do not truncate silently), ≤ 64 files;
  - write the table entry at `512 + i*64`
    (`name.ljust(24, b"\x00") + struct.pack("<IIII", 1, size, start, 128)`),
    write content at `start * 512`, bump `start` by 128 sectors.
- `next_free_sector` starts at 16 and ends at `16 + 128 * n_files`.
- Prints a summary: image path, size, files added with sizes.
- Errors are raised, not swallowed (`sys.exit` with a message), matching
  build.py's style.

Round-trip self-check (recommended, ~10 lines): after writing, re-open the
image, re-parse the superblock and table, and compare against what was
intended. Cheap insurance for the format's canonical implementation.

### 5.2 `kernel/build.py` integration

- `DISK = os.path.join(BUILD, "disk.img")`.
- New command `disk`: create `DISK` via mkfs **iff missing** (so user files
  written from inside the OS survive rebuilds; `python kernel/mkfs.py`
  directly is the explicit "wipe it" path). Seed it with one file, e.g.
  `--add` of a generated `hello.txt` ("this file lives on the MortFS disk"),
  so first boot's `ls` shows something. Implement by importing mkfs as a
  module (`import mkfs; mkfs.make(...)`) rather than shelling out.
- `_run(fullscreen)` and `run_iso()`: call the same ensure-disk helper, then
  append `["-hda", DISK]` to the QEMU argv. `-hda` composes fine with both
  `-kernel` and `-cdrom`.
- `check` stays disk-agnostic.

---

## 6. Implementation order (each step boots in QEMU before the next)

1. **Widen the input line** (Section 2.7, kmain.mx only).
   *Test*: `run`, type a 70-char `echo`, verify no truncation at 30; recall
   it with Up; check history slots don't bleed into each other (fill all 8).
2. **Land `inw`/`outw` in the compiler** (parallel track; blocks step 4).
   *Test*: hosted `--emit-c` of a probe using both; verify generated helpers
   mirror `mort_inb`/`mort_outb` with `uint16_t` value type.
3. **`mkfs.py` + `build.py disk`**.
   *Test (host only)*: create an image with two seeded files; hexdump
   sector 0 (magic `4D 46 53 31`), the two table entries at 0x200/0x240, and
   content at sector 16 / 144 (`0x2000` / `0x12000`); run the round-trip
   self-check.
4. **ATA driver + a temporary `disktest` command** that reads LBA 0 into
   SECBUF and prints the first 4 bytes as hex plus `ata_init`'s verdict.
   *Test*: boot with `-hda disk.img` → magic bytes appear; boot *without*
   `-hda` → `no disk`, shell stays alive (no hang: this is the timeout /
   0xFF-detection test). Also write a sector and hexdump the image on the
   host afterwards to confirm persistence + flush.
5. **`fs_init` + `ls` + `mods` rename**.
   *Test*: seeded files listed with correct sizes; `mods` still lists
   modules on the ISO path; boot with a zeroed (non-mkfs) image → `bad
   filesystem` message, no crash.
6. **`cat`**.
   *Test*: `cat` a seeded multi-line file (scrolling, newlines); `cat nope`
   → `not found: nope`.
7. **`write` + `rm`** (fs_create, fs_append_line, fs_remove, fs_sync).
   *Test*: create, append twice, `cat` shows both lines in order; `rm`,
   `ls` no longer shows it; **reboot QEMU and verify persistence**; fill a
   file to 64 KiB (host-seeded) and confirm one more `write` says
   `file full`; create until `file table full` path triggers (seed 63 files
   with mkfs to make this fast).
8. **`run <name>`** (run_range_lines split + depth guard).
   *Test*: author a script entirely in-OS with three `write` lines, `run`
   it; a script containing `run itself.txt` prints the nested-run error and
   continues; boot-time startup.txt still runs.
9. **Remove `disktest`**, update `help`, and run the full matrix:
   `build.py run` with disk, without disk, and `run-iso` with disk.

## 7. Explicit non-goals / v1 limitations (for the README)

- No space reclamation after `rm` (Section 2.6) — re-mkfs to compact.
- No directories, no rename, no partial writes, single drive, single bus.
- Disk I/O runs with interrupts off; PIT ticks are lost during transfers.
- Nested `run` is rejected.
- No consistency story if power dies mid-`fs_sync` (metadata is 9 sequential
  sector writes). Acceptable: QEMU, hobby OS.
