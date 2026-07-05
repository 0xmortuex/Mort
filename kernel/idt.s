/* GDT, IDT, PIC remap and interrupt stubs for MORT OS.
 *
 * kernel_setup (called from boot.s before mort_kmain) installs a flat GDT,
 * remaps the PICs so IRQs land at vectors 0x20+, and loads an IDT whose
 * keyboard gate (vector 0x21 = IRQ1) points at keyboard_isr. That stub calls
 * the Mort handler mort_on_key, then sends the PIC an end-of-interrupt.
 */

.set TIMER_VECTOR, 0x20
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

/* addresses of the 32 exception stubs, so load_idt can install them in a loop */
isr_table:
    .long isr0,  isr1,  isr2,  isr3,  isr4,  isr5,  isr6,  isr7
    .long isr8,  isr9,  isr10, isr11, isr12, isr13, isr14, isr15
    .long isr16, isr17, isr18, isr19, isr20, isr21, isr22, isr23
    .long isr24, isr25, isr26, isr27, isr28, isr29, isr30, isr31

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
    mov $idt_start, %edi              /* vectors 0..31: CPU exceptions       */
    mov $isr_table, %esi             /* each gets its own stub (records vec) */
    mov $32, %ecx
.fill_exc:
    mov (%esi), %eax
    call set_gate
    add $8, %edi
    add $4, %esi
    dec %ecx
    jnz .fill_exc

    mov $224, %ecx                    /* vectors 32..255: IRQ-style default  */
.fill_irq:                            /* (edi already points at entry 32)    */
    mov $default_isr, %eax
    call set_gate
    add $8, %edi
    dec %ecx
    jnz .fill_irq

    mov $idt_start, %edi              /* keyboard gate (IRQ1, vector 0x21)   */
    add $(KBD_VECTOR * 8), %edi
    mov $keyboard_isr, %eax
    call set_gate

    mov $idt_start, %edi              /* timer gate (IRQ0, vector 0x20)      */
    add $(TIMER_VECTOR * 8), %edi
    mov $timer_isr, %eax
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
    movb $0xFC, %al                   /* master: enable IRQ0 (timer) + IRQ1  */
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

.global timer_isr
.type timer_isr, @function
timer_isr:
    pusha
    call mort_on_tick                 /* the Mort handler bumps the tick count */
    movb $0x20, %al                   /* end-of-interrupt to the master PIC    */
    outb %al, $0x20
    popa
    iret

default_isr:
    pusha
    movb $0x20, %al
    outb %al, $0x20
    popa
    iret

/* CPU exceptions (vectors 0..31). Each vector has its own stub that pushes its
 * number, so the common handler knows which fault occurred. None are IRQs and
 * several push an error code, but we never iret (we report and halt), so the
 * exact frame layout doesn't matter — the vector we pushed is always on top. */
.macro ISR_STUB num
isr\num:
    push $\num
    jmp isr_common
.endm

ISR_STUB 0
ISR_STUB 1
ISR_STUB 2
ISR_STUB 3
ISR_STUB 4
ISR_STUB 5
ISR_STUB 6
ISR_STUB 7
ISR_STUB 8
ISR_STUB 9
ISR_STUB 10
ISR_STUB 11
ISR_STUB 12
ISR_STUB 13
ISR_STUB 14
ISR_STUB 15
ISR_STUB 16
ISR_STUB 17
ISR_STUB 18
ISR_STUB 19
ISR_STUB 20
ISR_STUB 21
ISR_STUB 22
ISR_STUB 23
ISR_STUB 24
ISR_STUB 25
ISR_STUB 26
ISR_STUB 27
ISR_STUB 28
ISR_STUB 29
ISR_STUB 30
ISR_STUB 31

isr_common:
    cli
    mov (%esp), %eax                 /* the vector number our stub pushed   */
    push %eax                        /* cdecl arg for mort_on_exception     */
    call mort_on_exception           /* Mort prints which fault it was      */
.hang:
    hlt                              /* no recovery: halt forever           */
    jmp .hang
