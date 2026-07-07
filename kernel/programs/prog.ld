/* Flat program image: everything at the fixed load base, entry first. */
ENTRY(_pstart)
SECTIONS {
    . = 0x00A00000;
    .text : {
        *(.start)
        *(.text*)
    }
    .rodata : { *(.rodata*) }
    .data   : { *(.data*) }
    .bss    : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
