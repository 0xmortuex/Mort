/* Program entry stub. The kernel calls the first byte of the flat binary
 * (load base 0x00A00000), so _pstart must be the very first thing linked.
 * It calls the Mort main and returns to the kernel. */
.section .start,"ax"
.global _pstart
_pstart:
    call mort_main
    ret
