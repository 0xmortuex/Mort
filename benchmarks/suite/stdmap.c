/* The C reference for stdmap.mx: the same linear-scan map with parallel, doubling arrays. */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    uint64_t *keys;
    uint64_t *values;
    uint64_t length;
    uint64_t capacity;
} Map;

int64_t find(const Map *map, uint64_t key) {
    for (uint64_t index = 0; index < map->length; index++) {
        if (map->keys[index] == key) {
            return (int64_t)index;
        }
    }
    return -1;
}

void insert(Map *map, uint64_t key, uint64_t value) {
    int64_t existing = find(map, key);
    if (existing >= 0) {
        map->values[existing] = value;
        return;
    }
    if (map->length == map->capacity) {
        uint64_t capacity = map->capacity * 2;
        uint64_t *keys = malloc(capacity * sizeof(uint64_t));
        uint64_t *values = malloc(capacity * sizeof(uint64_t));
        if (!keys || !values) {
            abort();
        }
        memcpy(keys, map->keys, map->length * sizeof(uint64_t));
        memcpy(values, map->values, map->length * sizeof(uint64_t));
        free(map->keys);
        free(map->values);
        map->keys = keys;
        map->values = values;
        map->capacity = capacity;
    }
    map->keys[map->length] = key;
    map->values[map->length] = value;
    map->length++;
}

int main(int argc, char **argv) {
    (void)argc;
    uint64_t n = strtoull(argv[1], NULL, 10);
    uint64_t modulus = 2147483648;
    uint64_t space = n / 2 + 1;
    Map table = {malloc(4 * sizeof(uint64_t)), malloc(4 * sizeof(uint64_t)), 0, 4};
    if (!table.keys || !table.values) {
        return 1;
    }
    uint64_t x = 42;
    for (uint64_t i = 0; i < n; i++) {
        x = (x * 1103515245 + 12345) % modulus;
        uint64_t key = x % space;
        int64_t found = find(&table, key);
        insert(&table, key, found >= 0 ? table.values[found] + 1 : 1);
    }
    uint64_t sum = 0, missing = 0;
    for (uint64_t key = 0; key < n; key++) {
        int64_t found = find(&table, key);
        if (found >= 0) {
            sum = (sum + table.values[found] * key) % modulus;
        } else {
            missing++;
        }
    }
    printf("%" PRIu64 "\n%" PRIu64 "\n%" PRIu64 "\n", sum, missing, table.length);
    free(table.keys);
    free(table.values);
    return 0;
}
