/* GDT, IDT, PIC remap and interrupt stubs for MORT OS.
 *
 * kernel_setup (called from boot.s before mort_kmain) installs a flat GDT,
 * remaps the PICs so IRQs land at vectors 0x20+, and loads an IDT whose
 * keyboard gate (vector 0x21 = IRQ1) points at keyboard_isr. That stub calls
 * the Mort handler mort_on_key, then sends the PIC an end-of-interrupt.
 */

.set KBD_VECTOR, 0x21

.section .data
.align 8

/* --- flat GDT: null, code (0x08), data (0x10) --- */
gdt_start:
    .quad 0x0000000000000000
    .word 0xFFFF, 0x0000
    .byte 0x00, 0x9A, 0xCF, 0x00      /* code: exec/read, ring0, 4K, 32-bit */
    .word 0xFFFF, 0x0000
    .byte 0x00, 0x92, 0xCF, 0x00      /* data: read/write, ring0, 4K, 32-bit */
gdt_end:
gdt_ptr:
    .word gdt_end - gdt_start - 1
    .long gdt_start

/* --- IDT: 256 gates, zeroed here and filled at runtime --- */
.align 8
idt_start:
    .rept 256
    .quad 0x0000000000000000
    .endr
idt_end:
idt_ptr:
    .word idt_end - idt_start - 1
    .long idt_start

.section .text

.global kernel_setup
.type kernel_setup, @function
kernel_setup:
    call load_gdt
    call remap_pic
    call load_idt
    ret

load_gdt:
    lgdt gdt_ptr
    ljmp $0x08, $.gdt_flush           /* reload CS via a far jump */
.gdt_flush:
    mov $0x10, %ax
    mov %ax, %ds
    mov %ax, %es
    mov %ax, %fs
    mov %ax, %gs
    mov %ax, %ss
    ret

/* set_gate: %edi -> 8-byte IDT entry, %eax = handler address */
set_gate:
    mov %ax, (%edi)                   /* offset 0..15  */
    movw $0x08, 2(%edi)               /* code selector */
    movb $0x00, 4(%edi)               /* reserved      */
    movb $0x8E, 5(%edi)               /* present, ring0, 32-bit interrupt gate */
    shr $16, %eax
    mov %ax, 6(%edi)                  /* offset 16..31 */
    ret

load_idt:
    mov $idt_start, %edi              /* fill every gate with default_isr */
    mov $256, %ecx
1:
    mov $default_isr, %eax
    call set_gate
    add $8, %edi
    dec %ecx
    jnz 1b

    mov $idt_start, %edi              /* then point the keyboard gate at us  */
    add $(KBD_VECTOR * 8), %edi
    mov $keyboard_isr, %eax
    call set_gate

    lidt idt_ptr
    ret

remap_pic:
    movb $0x11, %al                   /* ICW1: begin init, expect ICW4       */
    outb %al, $0x20
    outb %al, $0xA0
    movb $0x20, %al                   /* ICW2: master vector offset 0x20     */
    outb %al, $0x21
    movb $0x28, %al                   /* ICW2: slave vector offset 0x28      */
    outb %al, $0xA1
    movb $0x04, %al                   /* ICW3: slave on master IRQ2          */
    outb %al, $0x21
    movb $0x02, %al
    outb %al, $0xA1
    movb $0x01, %al                   /* ICW4: 8086 mode                     */
    outb %al, $0x21
    outb %al, $0xA1
    movb $0xFD, %al                   /* mask all master IRQs except IRQ1    */
    outb %al, $0x21
    movb $0xFF, %al                   /* mask all slave IRQs                 */
    outb %al, $0xA1
    ret

.global keyboard_isr
.type keyboard_isr, @function
keyboard_isr:
    pusha
    call mort_on_key                  /* the Mort handler reads the scancode */
    movb $0x20, %al                   /* end-of-interrupt to the master PIC  */
    outb %al, $0x20
    popa
    iret

default_isr:
    pusha
    movb $0x20, %al
    outb %al, $0x20
    popa
    iret
