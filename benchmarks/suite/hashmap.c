/* Hash map workload, the C reference for hashmap.mx: the same open-addressing table. */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>

uint64_t locate(uint64_t *keys, uint64_t mask, uint64_t key) {
    uint64_t slot = ((key + 1) * UINT64_C(11400714819323198485)) >> 40 & mask;
    while (keys[slot] != 0 && keys[slot] != key + 1) {
        slot = (slot + 1) & mask;
    }
    return slot;
}

int main(int argc, char **argv) {
    (void)argc;
    uint64_t n = strtoull(argv[1], NULL, 10);
    uint64_t modulus = 2147483648;
    uint64_t space = n / 2 + 1;
    uint64_t capacity = 16;
    while (capacity < n * 2) {
        capacity *= 2;
    }
    uint64_t mask = capacity - 1;
    uint64_t *keys = malloc(capacity * sizeof(uint64_t));
    uint64_t *values = malloc(capacity * sizeof(uint64_t));
    if (!keys || !values) {
        return 1;
    }
    for (uint64_t i = 0; i < capacity; i++) {
        keys[i] = 0;
        values[i] = 0;
    }
    uint64_t used = 0;
    uint64_t x = 42;
    for (uint64_t i = 0; i < n; i++) {
        x = (x * 1103515245 + 12345) % modulus;
        uint64_t key = x % space;
        uint64_t slot = locate(keys, mask, key);
        if (keys[slot] == 0) {
            keys[slot] = key + 1;
            used++;
        }
        values[slot] = values[slot] + 1;
    }
    uint64_t sum = 0, missing = 0;
    for (uint64_t key = 0; key < n; key++) {
        uint64_t slot = locate(keys, mask, key);
        if (keys[slot] != 0) {
            sum = (sum + values[slot] * key) % modulus;
        } else {
            missing++;
        }
    }
    printf("%" PRIu64 "\n%" PRIu64 "\n%" PRIu64 "\n", sum, missing, used);
    free(keys);
    free(values);
    return 0;
}
