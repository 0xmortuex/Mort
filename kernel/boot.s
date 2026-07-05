/* Multiboot1 header + 32-bit entry stub for MORT OS.
 *
 * QEMU's built-in multiboot loader (qemu-system-i386 -kernel) reads the header,
 * loads us at 1 MB in 32-bit protected mode, and jumps to _start. We set up a
 * stack and call the kernel written in Mort (mort_kmain), then halt forever.
 */

.set ALIGN,    1 << 0             /* align loaded modules on page boundaries */
.set MEMINFO,  1 << 1             /* provide a memory map                    */
.set MB_FLAGS, ALIGN | MEMINFO
.set MB_MAGIC, 0x1BADB002
.set MB_CHECK, -(MB_MAGIC + MB_FLAGS)

/* "a" = allocatable: without it the section is not part of the load image, so
 * the linker won't actually place the header first (it drifts by file offset). */
.section .multiboot, "a", @progbits
.align 4
.long MB_MAGIC
.long MB_FLAGS
.long MB_CHECK

/* A small stack. The System V ABI wants it 16-byte aligned. */
.section .bss
.align 16
stack_bottom:
.skip 16384                       /* 16 KiB */
stack_top:

.section .text
.global _start
.type _start, @function
_start:
    mov $stack_top, %esp          /* set up the stack               */
    call kernel_setup             /* GDT + PIC remap + IDT (idt.s)  */
    push %ebx                     /* multiboot info ptr -> kmain arg */
    call mort_kmain               /* enter the Mort kernel          */
    cli                           /* if it returns, hang safely     */
.Lhang:
    hlt
    jmp .Lhang
.size _start, . - _start
