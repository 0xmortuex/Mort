/* Link the MORT OS kernel as a flat 32-bit image loaded at 1 MB.
 * The multiboot header must sit in the first 8 KiB, so .multiboot goes first
 * (KEEP stops the linker from garbage-collecting it — nothing references it). */
ENTRY(_start)

SECTIONS
{
    . = 1M;

    .multiboot : { KEEP(*(.multiboot)) }
    .text      : { *(.text*) }
    .rodata    : { *(.rodata*) }
    .data      : { *(.data*) }
    .bss       : { *(.bss*) *(COMMON) }
}
