#!/usr/bin/env sh
# Build the nanomort kernel example and boot it in QEMU.
#
# Requirements:
#   - python3                (runs the compiler)
#   - a 32-bit-capable C compiler. Easiest cross-compiler:
#       pip install ziglang   ->   use `zig cc` (works from any host/OS)
#     or gcc/clang with 32-bit support (`gcc -m32`, needs multilib on Linux).
#   - qemu-system-i386        (to boot it)
#
# Set CC to your compiler. Examples:
#   CC="python3 -m ziglang cc -target x86-freestanding"   ./build.sh
#   CC="gcc -m32"                                          ./build.sh
set -e
cd "$(dirname "$0")"

CC="${CC:-gcc -m32}"
CFLAGS="-ffreestanding -nostdlib -fno-stack-protector -fno-pic -fno-pie -Wall"

echo ">> compiling kmain.nm -> kmain.c (freestanding)"
python3 ../nanomort.py kmain.nm --freestanding --emit-c > build_kmain.c

echo ">> assembling + compiling with: $CC"
mkdir -p build
$CC $CFLAGS -c build_kmain.c -o build/kmain.o
$CC $CFLAGS -c boot.s        -o build/boot.o

echo ">> linking at 1 MB"
# Use the C compiler as the linker driver so it finds the right ld.
# -static -no-pie so the result is a plain load-at-1MB image, not a PIE.
$CC -nostdlib -static -no-pie -Wl,-melf_i386 -Wl,-T,linker.ld \
    build/boot.o build/kmain.o -o build/kernel.elf

echo ">> booting in QEMU (close the window or Ctrl-A X to quit)"
qemu-system-i386 -kernel build/kernel.elf
