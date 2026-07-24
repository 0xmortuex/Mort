/* Multiboot1 header + 32-bit entry stub for the nanomort kernel example.
 *
 * A multiboot bootloader (QEMU's built-in -kernel loader, or GRUB) reads the
 * header below, loads us at 1 MB in 32-bit protected mode, and jumps to
 * _start. We set up a stack and call nm_kmain — the entry point the nanomort
 * compiler emits from kmain.nm — then halt if it ever returns.
 */

.set MB_MAGIC, 0x1BADB002
.set MB_FLAGS, 0x00000003          /* align modules + provide a memory map */
.set MB_CHECK, -(MB_MAGIC + MB_FLAGS)

/* "a" = allocatable, so the linker actually places this in the load image
 * (and thus in the first 8 KiB, where the bootloader scans for it). */
.section .multiboot, "a", @progbits
.align 4
.long MB_MAGIC
.long MB_FLAGS
.long MB_CHECK

.section .bss
.align 16
stack_bottom:
.skip 16384                        /* 16 KiB stack */
stack_top:

.section .text
.global _start
.type _start, @function
_start:
    mov $stack_top, %esp           /* there is no stack yet — make one */
    call nm_kmain                  /* enter the kernel, compiled from nanomort */
    cli
.Lhang:
    hlt
    jmp .Lhang
.size _start, . - _start
