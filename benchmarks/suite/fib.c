/* Recursive Fibonacci, the C reference for fib.mx. */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>

int64_t fib(int64_t n) {
    if (n < 2) {
        return n;
    }
    return fib(n - 1) + fib(n - 2);
}

int main(int argc, char **argv) {
    (void)argc;
    printf("%" PRId64 "\n", fib(strtoll(argv[1], NULL, 10)));
    return 0;
}
