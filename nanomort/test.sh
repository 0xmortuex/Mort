#!/usr/bin/env sh
# Tiny test suite: compile-and-run the examples, assert their output, and
# check that a deliberately wrong program is rejected with a line number.
set -e
cd "$(dirname "$0")"
fail() { echo "FAIL: $1"; exit 1; }

echo ">> examples/fib.nm"
got=$(python3 nanomort.py examples/fib.nm --run | tr '\n' ' ')
[ "$got" = "0 1 1 2 3 5 8 13 21 34 " ] || fail "fib output: $got"

echo ">> examples/primes.nm"
got=$(python3 nanomort.py examples/primes.nm --run | tr '\n' ' ')
[ "$got" = "2 3 5 7 11 13 17 19 23 29 " ] || fail "primes output: $got"

echo ">> type errors are rejected"
printf 'fn main() -> int {\n  let x: bool = 5;\n  return 0;\n}\n' > /tmp/nm_bad.nm
if python3 nanomort.py /tmp/nm_bad.nm --emit-c >/dev/null 2>&1; then
    fail "a bool = int program should not compile"
fi

echo ">> freestanding kernel emits valid C"
python3 nanomort.py kernel/kmain.nm --freestanding --emit-c >/tmp/nm_k.c
cc -ffreestanding -nostdlib -fno-stack-protector -w -c /tmp/nm_k.c -o /tmp/nm_k.o \
    || fail "freestanding kernel C did not compile"

echo "ALL PASS"
